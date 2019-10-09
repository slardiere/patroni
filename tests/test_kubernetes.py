import time
import unittest

from mock import Mock, patch
from patroni.dcs.kubernetes import Kubernetes, KubernetesError, k8s_client, k8s_watch, RetryFailedError
from threading import Thread
from . import SleepException


def mock_list_namespaced_config_map(self, *args, **kwargs):
    metadata = {'resource_version': '1', 'labels': {'f': 'b'}, 'name': 'test-config',
                'annotations': {'initialize': '123', 'config': '{}'}}
    items = [k8s_client.V1ConfigMap(metadata=k8s_client.V1ObjectMeta(**metadata))]
    metadata.update({'name': 'test-leader', 'annotations': {'optime': '1234', 'leader': 'p-0', 'ttl': '30s'}})
    items.append(k8s_client.V1ConfigMap(metadata=k8s_client.V1ObjectMeta(**metadata)))
    metadata.update({'name': 'test-failover', 'annotations': {'leader': 'p-0'}})
    items.append(k8s_client.V1ConfigMap(metadata=k8s_client.V1ObjectMeta(**metadata)))
    metadata.update({'name': 'test-sync', 'annotations': {'leader': 'p-0'}})
    items.append(k8s_client.V1ConfigMap(metadata=k8s_client.V1ObjectMeta(**metadata)))
    metadata = k8s_client.V1ObjectMeta(resource_version='1')
    return k8s_client.V1ConfigMapList(metadata=metadata, items=items)


def mock_list_namespaced_pod(self, *args, **kwargs):
    metadata = k8s_client.V1ObjectMeta(resource_version='1', name='p-0', annotations={'status': '{}'})
    items = [k8s_client.V1Pod(metadata=metadata)]
    return k8s_client.V1PodList(items=items)


@patch.object(k8s_client.CoreV1Api, 'patch_namespaced_config_map', Mock())
@patch.object(k8s_client.CoreV1Api, 'create_namespaced_config_map', Mock())
@patch.object(Thread, 'start', Mock())
class TestKubernetes(unittest.TestCase):

    @patch('kubernetes.config.load_kube_config', Mock())
    @patch.object(k8s_client.CoreV1Api, 'list_namespaced_config_map', mock_list_namespaced_config_map)
    @patch.object(k8s_client.CoreV1Api, 'list_namespaced_pod', mock_list_namespaced_pod)
    @patch.object(Thread, 'start', Mock())
    def setUp(self):
        self.k = Kubernetes({'ttl': 30, 'scope': 'test', 'name': 'p-0', 'retry_timeout': 10, 'labels': {'f': 'b'}})
        self.k.get_cluster()

    def test_get_cluster(self):
        with patch.object(k8s_client.CoreV1Api, 'list_namespaced_config_map', mock_list_namespaced_config_map), \
                patch.object(k8s_client.CoreV1Api, 'list_namespaced_pod', mock_list_namespaced_pod), \
                patch('time.time', Mock(return_value=time.time() + 31)):
            self.k.get_cluster()

        with patch.object(k8s_client.CoreV1Api, 'list_namespaced_pod', Mock(side_effect=Exception)):
            self.assertRaises(KubernetesError, self.k.get_cluster)

    @patch('kubernetes.config.load_kube_config', Mock())
    @patch.object(k8s_client.CoreV1Api, 'create_namespaced_endpoints', Mock())
    def test_update_leader(self):
        k = Kubernetes({'ttl': 30, 'scope': 'test', 'name': 'p-0', 'retry_timeout': 10,
                        'labels': {'f': 'b'}, 'use_endpoints': True, 'pod_ip': '10.0.0.0'})
        self.assertIsNotNone(k.update_leader('123'))

    @patch('kubernetes.config.load_kube_config', Mock())
    @patch.object(k8s_client.CoreV1Api, 'create_namespaced_endpoints', Mock())
    def test_update_leader_with_restricted_access(self):
        k = Kubernetes({'ttl': 30, 'scope': 'test', 'name': 'p-0', 'retry_timeout': 10,
                        'labels': {'f': 'b'}, 'use_endpoints': True, 'pod_ip': '10.0.0.0'})
        self.assertIsNotNone(k.update_leader('123', True))

    def test_take_leader(self):
        self.k.take_leader()
        self.k._leader_observed_record['leader'] = 'test'
        self.k.patch_or_create = Mock(return_value=False)
        self.k.take_leader()

    def test_manual_failover(self):
        with patch.object(k8s_client.CoreV1Api, 'patch_namespaced_config_map', Mock(side_effect=RetryFailedError(''))):
            self.k.manual_failover('foo', 'bar')

    def test_set_config_value(self):
        self.k.set_config_value('{}')

    @patch.object(k8s_client.CoreV1Api, 'patch_namespaced_pod', Mock(return_value=True))
    def test_touch_member(self):
        self.k.touch_member({'role': 'replica'})
        self.k._name = 'p-1'
        self.k.touch_member({'state': 'running', 'role': 'replica'})
        self.k.touch_member({'state': 'stopped', 'role': 'master'})

    def test_initialize(self):
        self.k.initialize()

    def test_delete_leader(self):
        self.k.delete_leader()

    def test_cancel_initialization(self):
        self.k.cancel_initialization()

    @patch.object(k8s_client.CoreV1Api, 'delete_collection_namespaced_config_map',
                  Mock(side_effect=k8s_client.rest.ApiException(403, '')))
    def test_delete_cluster(self):
        self.k.delete_cluster()

    @patch('kubernetes.config.load_kube_config', Mock())
    @patch.object(k8s_client.CoreV1Api, 'create_namespaced_endpoints',
                  Mock(side_effect=[k8s_client.rest.ApiException(502, ''), k8s_client.rest.ApiException(500, '')]))
    def test_delete_sync_state(self):
        k = Kubernetes({'ttl': 30, 'scope': 'test', 'name': 'p-0', 'retry_timeout': 10,
                        'labels': {'f': 'b'}, 'use_endpoints': True, 'pod_ip': '10.0.0.0'})
        self.assertFalse(k.delete_sync_state())

    @patch.object(k8s_watch.Watch, 'stream', Mock(return_value=[{'raw_object': {'metadata': {}}}]))
    def test_start_watch_stream(self):
        self.assertIsNotNone(self.k.start_watch_stream())

    def test_watch(self):
        self.k.set_ttl(10)
        self.k.watch(None, 0)
        self.k.watch(None, 0)

    def test_set_history_value(self):
        self.k.set_history_value('{}')

    @patch('kubernetes.config.load_kube_config', Mock())
    @patch.object(k8s_client.CoreV1Api, 'patch_namespaced_pod', Mock(return_value=True))
    @patch.object(k8s_client.CoreV1Api, 'create_namespaced_endpoints', Mock())
    @patch.object(k8s_client.CoreV1Api, 'create_namespaced_service',
                  Mock(side_effect=[True, False, k8s_client.rest.ApiException(500, '')]))
    def test__create_config_service(self):
        k = Kubernetes({'ttl': 30, 'scope': 'test', 'name': 'p-0', 'retry_timeout': 10,
                        'labels': {'f': 'b'}, 'use_endpoints': True, 'pod_ip': '10.0.0.0'})
        self.assertIsNotNone(k.patch_or_create_config({'foo': 'bar'}))
        self.assertIsNotNone(k.patch_or_create_config({'foo': 'bar'}))
        k.touch_member({'state': 'running', 'role': 'replica'})


@patch('time.sleep', Mock(side_effect=SleepException))
@patch.object(Kubernetes, 'start_watch_stream')
class TestKubernetesWatcher(unittest.TestCase):

    @patch('kubernetes.config.load_kube_config', Mock())
    @patch.object(Thread, 'start', Mock())
    def setUp(self):
        self.k = Kubernetes({'ttl': 30, 'scope': 'test', 'name': 'p-0', 'retry_timeout': 10, 'labels': {'f': 'b'}})

    def test_leader_update(self, mock_stream):
        mock_stream.return_value = [
            {'raw_object': {'type': 'MODIFIED',
                            'metadata': {'name': self.k.leader_path, 'annotations': {self.k._LEADER: 'foo'}}}},
            {'raw_object': {'type': 'MODIFIED',
                            'metadata': {'name': self.k.leader_path, 'annotations': {self.k._LEADER: 'bar'}}}}
        ]
        self.assertRaises(SleepException, self.k._watcher.run)

    def test_config_update(self, mock_stream):
        mock_stream.return_value = [
            {'raw_object': {'type': 'MODIFIED',
                            'metadata': {'name': self.k.config_path, 'annotations': {self.k._CONFIG: 'foo'}}}},
            {'raw_object': {'type': 'MODIFIED',
                            'metadata': {'name': self.k.config_path, 'annotations': {self.k._CONFIG: 'bar'}}}}
        ]
        self.assertRaises(SleepException, self.k._watcher.run)

    def test_run(self, mock_stream):
        mock_stream.side_effect = Exception
        self.assertRaises(SleepException, self.k._watcher.run)
