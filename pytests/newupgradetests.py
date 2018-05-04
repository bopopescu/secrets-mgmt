import Queue
import copy
from newupgradebasetest import NewUpgradeBaseTest
from remote.remote_util import RemoteMachineShellConnection, RemoteUtilHelper
from couchbase_helper.documentgenerator import BlobGenerator
from membase.api.rest_client import RestConnection, RestHelper
from membase.api.exception import RebalanceFailedException
from membase.helper.cluster_helper import ClusterOperationHelper
from memcached.helper.kvstore import KVStore
# from 2i.indexscans_2i import SecondaryIndexingScanTests
from testconstants import COUCHBASE_VERSION_2
from testconstants import COUCHBASE_VERSION_3, COUCHBASE_FROM_VERSION_3
from testconstants import SHERLOCK_VERSION


class SingleNodeUpgradeTests(NewUpgradeBaseTest):
    def setUp(self):
        super(SingleNodeUpgradeTests, self).setUp()
        self.queue = Queue.Queue()

    def tearDown(self):
        super(SingleNodeUpgradeTests, self).tearDown()
        if self.input.param("op", None) == "close_port":
            remote = RemoteMachineShellConnection(self.master)
            remote.disable_firewall()

    def test_upgrade(self):
        self._install([self.master])
        self.operations([self.master])
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            upgrade_threads = self._async_update(upgrade_version, [self.master])
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            self.add_built_in_server_user(node=self.master)
            self.sleep(self.expire_time)
            #            if not self.is_linux:
            #                self.wait_node_restarted(self.master, wait_time=1200, wait_if_warmup=True, check_service=True)
            remote = RemoteMachineShellConnection(self.master)
            for bucket in self.buckets:
                remote.execute_cbepctl(bucket, "", "set flush_param", "exp_pager_stime", 5)
            remote.disconnect()
            self.sleep(30)
            self.verification([self.master])

    def test_upgrade_negative(self):
        op = self.input.param("op", None)
        error = self.input.param("error", '')
        remote = RemoteMachineShellConnection(self.master)
        if op is None:
            self.fail("operation should be specified")
        if op == "higher_version":
            tmp = self.initial_version
            self.initial_version = self.upgrade_versions[0]
            self.upgrade_versions = [tmp, ]
        info = None
        if op == "wrong_arch":
            info = remote.extract_remote_info()
            info.architecture_type = ('x86_64', 'x86')[info.architecture_type == 'x86']
        self._install([self.master])
        self.operations([self.master])
        try:
            if op == "close_port":
                RemoteUtilHelper.enable_firewall(self.master)
            for upgrade_version in self.upgrade_versions:
                self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                           format(upgrade_version))
                output, error = self._upgrade(upgrade_version, self.master, info=info)
                if str(output).find(error) != -1 or str(error).find(error) != -1:
                    raise Exception(error)
        except Exception, ex:
            self.log.info("Exception %s appeared as expected" % ex)
            self.log.info("Check that old version is working fine")
            self.verification([self.master])
        else:
            self.fail("Upgrade should fail!")
        remote.disconnect()


class Upgrade_Utils(NewUpgradeBaseTest):
    def setUp(self):
        super(Upgrade_Utils, self).setUp()
        self.nodes_init = self.input.param('nodes_init', 2)
        self.queue = Queue.Queue()

    def tearDown(self):
        print "teardown done"

    def add_and_rebalance(self):
        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        self.initial_version = self.upgrade_versions[0]
        self.product = 'couchbase-server'
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        self._install(self.servers[self.nodes_init:self.num_servers])


class MultiNodesUpgradeTests(NewUpgradeBaseTest):
    def setUp(self):
        super(MultiNodesUpgradeTests, self).setUp()
        self.nodes_init = self.input.param('nodes_init', 2)
        self.queue = Queue.Queue()

    def tearDown(self):
        super(MultiNodesUpgradeTests, self).tearDown()

    def offline_cluster_upgrade(self):
        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        num_stoped_nodes = self.input.param('num_stoped_nodes', self.nodes_init)
        upgrade_nodes = self.servers[self.nodes_init - num_stoped_nodes:self.nodes_init]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()
            upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.add_built_in_server_user()
            self.sleep(self.expire_time)
            if self.during_ops:
                if "add_back_failover" in self.during_ops:
                    getattr(self, 'add_back_failover')()
                    self.cluster.rebalance(self.servers[:self.nodes_init], [], [])
                elif "failover" in self.during_ops:
                    self.cluster.rebalance(self.servers[:self.nodes_init], [], [])
                    rem = [server for server in self.servers[:self.nodes_init]
                           if self.failover_node.ip == server.ip and str(self.failover_node.port) == server.port]
                    self.dcp_rebalance_in_offline_upgrade_from_version2()
                    self.verification(list(set(self.servers[:self.nodes_init]) - set(rem)))
                    return
            self.dcp_rebalance_in_offline_upgrade_from_version2()
            self._create_ephemeral_buckets()
            self.verification(self.servers[:self.nodes_init])
            if self.input.param('check_seqno', True):
                self.check_seqno(seqno_expected)

    def offline_cluster_upgrade_and_reboot(self):
        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        if self.during_ops:
            for opn in self.during_ops:
                getattr(self, opn)()
        num_stoped_nodes = self.input.param('num_stoped_nodes', self.nodes_init)
        stoped_nodes = self.servers[self.nodes_init - num_stoped_nodes:self.nodes_init]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in stoped_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                remote.disconnect()
            self.sleep(self.sleep_time)
            upgrade_threads = self._async_update(upgrade_version, stoped_nodes)
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            self.dcp_rebalance_in_offline_upgrade_from_version2()
            self.add_built_in_server_user()
            for server in stoped_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(5)
                remote.start_couchbase()
                remote.disconnect()
            ClusterOperationHelper.wait_for_ns_servers_or_assert(stoped_nodes, self)
            self.log.info("Need to sleep 15 seconds for couchbase server startup completely")
            self.sleep(15)
            self._create_ephemeral_buckets()
            self.verification(self.servers[:self.nodes_init])

    def offline_cluster_upgrade_and_rebalance(self):
        num_stoped_nodes = self.input.param('num_stoped_nodes', self.nodes_init)
        stoped_nodes = self.servers[self.nodes_init - num_stoped_nodes:self.nodes_init]
        servs_out = self.servers[self.nodes_init - num_stoped_nodes - self.nodes_out:self.nodes_init - num_stoped_nodes]
        servs_in = self.servers[self.nodes_init:self.nodes_init + self.nodes_in]
        self._install(self.servers)
        self.operations(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        if self.during_ops:
            for opn in self.during_ops:
                getattr(self, opn)()
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in stoped_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                remote.disconnect()
            upgrade_threads = self._async_update(upgrade_version, stoped_nodes)
            try:
                self.cluster.rebalance(self.servers[:self.nodes_init], servs_in, servs_out)
            except RebalanceFailedException:
                self.log.info("rebalance failed as expected")
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            ClusterOperationHelper.wait_for_ns_servers_or_assert(stoped_nodes, self)
            self.cluster.rebalance(self.servers[:self.nodes_init], [], servs_out)
            self.dcp_rebalance_in_offline_upgrade_from_version2()
            self._create_ephemeral_buckets()
            self.verification(list(set(self.servers[:self.nodes_init] + servs_in) - set(servs_out)))

    def offline_cluster_upgrade_non_default_path(self):
        try:
            num_nodes_with_not_default = self.input.param('num_nodes_with_not_default', 1)
            prefix_path = ''
            if not self.is_linux:
                prefix_path = "C:"
            data_path = prefix_path + self.input.param('data_path', '/tmp/data').replace('|', "/")
            index_path = self.input.param('index_path', data_path).replace('|', "/")
            if not self.is_linux and not index_path.startswith("C:"):
                index_path = prefix_path + index_path
            num_nodes_remove_data = self.input.param('num_nodes_remove_data', 0)
            servers_with_not_default = self.servers[:num_nodes_with_not_default]
            old_paths = {}
            for server in servers_with_not_default:
                # to restore servers paths in finally
                old_paths[server.ip] = [server.data_path, server.index_path]
                server.data_path = data_path
                server.index_path = index_path
                shell = RemoteMachineShellConnection(server)
                # shell.remove_folders([data_path, index_path])
                for path in set([data_path, index_path]):
                    shell.create_directory(path)
                shell.disconnect()
            self._install(self.servers[:self.nodes_init])
            self.operations(self.servers[:self.nodes_init])
            if self.ddocs_num and not self.input.param('extra_verification', False):
                self.create_ddocs_and_views()
            for upgrade_version in self.upgrade_versions:
                self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                           format(upgrade_version))
                for server in self.servers[:self.nodes_init]:
                    remote = RemoteMachineShellConnection(server)
                    remote.stop_server()
                    remote.disconnect()
                self.sleep(self.sleep_time)
                # remove data for nodes with non default data paths
                tmp = min(num_nodes_with_not_default, num_nodes_remove_data)
                self.delete_data(self.servers[:tmp], [data_path + "/*", index_path + "/*"])
                # remove data for nodes with default data paths
                self.delete_data(self.servers[tmp: max(tmp, num_nodes_remove_data)],
                                 ["/opt/couchbase/var/lib/couchbase/data/*"])
                upgrade_threads = self._async_update(upgrade_version, self.servers[:self.nodes_init])
                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed!")
                self.dcp_rebalance_in_offline_upgrade_from_version2()
                self.add_built_in_server_user()
                self.sleep(self.expire_time)
                for server in servers_with_not_default:
                    rest = RestConnection(server)
                    node = rest.get_nodes_self()
                    print node.storage[0]
                    self.assertEqual(node.storage[0].path.lower(), data_path.lower(),
                                     "Server %s. Data path expected:%s, actual %s." % (
                                         server.ip, data_path, node.storage[0].path))
                    self.assertEqual(node.storage[0].index_path.lower(), index_path.lower(),
                                     "Server %s. Index path expected: %s, actual: %s." % (
                                         server.ip, index_path, node.storage[0].index_path))
            if num_nodes_remove_data:
                for bucket in self.buckets:
                    if self.rest_helper.bucket_exists(bucket):
                        raise Exception("bucket: %s still exists" % bucket.name)
                self.buckets = []

            if self.input.param('extra_verification', False):
                self.bucket_size = 100
                self._create_sasl_buckets(self.master, 1)
                self._create_standard_buckets(self.master, 1)
                if self.ddocs_num:
                    self.create_ddocs_and_views()
                    gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                    self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
            self._create_ephemeral_buckets()
            self.verification(self.servers[:self.nodes_init], check_items=not num_nodes_remove_data)
        finally:
            for server in servers_with_not_default:
                server.data_path = old_paths[server.ip][0]
                server.index_path = old_paths[server.ip][1]

    def offline_cluster_upgrade_with_reinstall(self):
        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        if self.during_ops:
            for opn in self.during_ops:
                getattr(self, opn)()
        num_nodes_reinstall = self.input.param('num_nodes_reinstall', 1)
        stoped_nodes = self.servers[self.nodes_init - (self.nodes_init - num_nodes_reinstall):self.nodes_init]
        nodes_reinstall = self.servers[:num_nodes_reinstall]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in stoped_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                remote.disconnect()
            self.sleep(self.sleep_time)
            upgrade_threads = self._async_update(upgrade_version, stoped_nodes)
            self.force_reinstall(nodes_reinstall)
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            self.dcp_rebalance_in_offline_upgrade_from_version2()
            self._create_ephemeral_buckets()
            self.verification(self.servers[:self.nodes_init])

    def online_upgrade_rebalance_in_with_ops(self):
        gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)

        def modify_data():
            tasks_ops = []
            for bucket in self.buckets:
                gen = copy.deepcopy(gen_load)
                tasks_ops.append(self.cluster.async_load_gen_docs(self.master, bucket.name, gen,
                                                                  bucket.kvs[1], "create"))
            for task in tasks_ops:
                task.result()

        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        self.product = 'couchbase-server'
        servs_in = self.servers[self.nodes_init:self.nodes_in + self.nodes_init]
        servs_out = self.servers[self.nodes_init - self.nodes_out:self.nodes_init]

        if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
            self._install(self.servers[self.nodes_init:self.num_servers], community_to_enterprise=True)
        else:
            self._install(self.servers[self.nodes_init:self.num_servers])
        self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")

        modify_data()
        task_reb = self.cluster.async_rebalance(self.servers[:self.nodes_init], servs_in, servs_out)
        self.add_built_in_server_user()
        while task_reb.state != "FINISHED":
            modify_data()
        task_reb.result()
        self._create_ephemeral_buckets()
        self.verification(list((set(self.servers[:self.nodes_init]) | set(servs_in)) - set(servs_out)))

    def online_upgrade_rebalance_in_out(self):
        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        seqno_expected = 1
        seqno_comparator = '>='
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
            seqno_comparator = '=='
        if self.ddocs_num:
            self.create_ddocs_and_views()
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        self.product = 'couchbase-server'
        if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
            self._install(self.servers[self.nodes_init:self.num_servers], community_to_enterprise=True)
        else:
            self._install(self.servers[self.nodes_init:self.num_servers])
        self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")
        if self.during_ops:
            for opn in self.during_ops:
                getattr(self, opn)()
        if self.wait_expire:
            self.sleep(self.expire_time)
            for bucket in self.buckets:
                bucket.kvs[1] = KVStore()
        self.online_upgrade()
        self.add_built_in_server_user()
        self.sleep(10)

        if self.input.param('initial_version', '')[:5] in COUCHBASE_FROM_VERSION_3:
            self.master = self.servers[self.nodes_init: self.num_servers][0]
        """ verify DCP upgrade in 3.0.0 version """
        self.monitor_dcp_rebalance()

        if self.input.param('reboot_cluster', False):
            self.warm_up_node(self.servers[self.nodes_init: self.num_servers])
        self._create_ephemeral_buckets()
        self.verification(self.servers[self.nodes_init: self.num_servers])
        if self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected, comparator=seqno_comparator)

    def online_upgrade_incremental(self):
        self._install(self.servers)
        self.operations(self.servers)
        if self.ddocs_num:
            self.create_ddocs_and_views()
        for server in self.servers[1:]:
            self.cluster.rebalance(self.servers, [], [server])
            self.initial_version = self.upgrade_versions[0]
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                       format(self.initial_version))
            self.product = 'couchbase-server'
            self._install([server])
            self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")
            self.cluster.rebalance(self.servers, [server], [])
            self.log.info("Rebalanced in upgraded nodes")
            self.sleep(self.sleep_time)
            self.verification(self.servers)
        self._new_master(self.servers[1])
        self.cluster.rebalance(self.servers, [], [self.servers[0]])
        self.log.info("Rebalanced out all old version nodes")
        self.sleep(10)

        """ verify DCP upgrade in 3.0.0 version """
        self.monitor_dcp_rebalance()
        self._create_ephemeral_buckets()
        self.verification(self.servers[1:])

    def online_consequentially_upgrade(self):
        half_node = len(self.servers) / 2
        self._install(self.servers[:half_node])
        self.operations(self.servers[:half_node])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        self.product = 'couchbase-server'
        self._install(self.servers[half_node:])
        self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")
        self.log.info("Rebalanced in upgraded nodes and rebalanced out nodes with old version")
        self.cluster.rebalance(self.servers, self.servers[half_node:], self.servers[:half_node])
        self.sleep(10)

        """ verify DCP upgrade in 3.x.x version """
        self.master = self.servers[half_node]
        self.add_built_in_server_user()
        self.monitor_dcp_rebalance()
        self.sleep(self.sleep_time)
        try:
            for server in self.servers[half_node:]:
                if self.port and self.port != '8091':
                    server.port = self.port
            self._new_master(self.servers[half_node])
            self.add_built_in_server_user()
            self.verification(self.servers[half_node:])
            self.log.info("Upgrade nodes of old version")
            upgrade_threads = self._async_update(self.upgrade_versions[0], self.servers[:half_node],
                                                 None, True)
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            self.cluster.rebalance(self.servers[half_node:], self.servers[:half_node], [])
            self.log.info("Rebalanced in all new version nodes")
            self.add_built_in_server_user()
            self.sleep(self.sleep_time)
            self._create_ephemeral_buckets()
            self.verification(self.servers)
        finally:
            for server in self.servers:
                server.port = '8091'

    def online_upgrade_and_rebalance(self):
        self._install(self.servers)
        self.operations(self.servers)
        if self.ddocs_num:
            self.create_ddocs_and_views()
        upgrade_servers = self.servers[self.nodes_init:self.num_servers]
        self.cluster.rebalance(self.servers, [], upgrade_servers)
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
            self._install(upgrade_servers, community_to_enterprise=True)
        else:
            self._install(upgrade_servers)
        self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")
        self.log.info("Rebalance in new version nodes and rebalance out some nodes")
        self.cluster.rebalance(self.servers, upgrade_servers, self.servers[self.num_servers:])
        self.log.info("Rebalance completed")
        self.log.info("Remove the second old version node")
        self._new_master(self.servers[1])
        self.cluster.rebalance(self.servers, [], [self.servers[0]])
        self.log.info("Rebalance completed")
        self.sleep(10)
        """ verify DCP upgrade in 3.0.0 version """
        self.master = self.servers[self.nodes_init]
        self.monitor_dcp_rebalance()
        self._create_ephemeral_buckets()
        self.verification(self.servers[self.nodes_init: -self.nodes_init])

    def online_upgrade(self, services=None):
        servers_in = self.servers[self.nodes_init:self.num_servers]
        self.cluster.rebalance(self.servers[:self.nodes_init], servers_in, [], services=services)
        self.log.info("Rebalance in all {0} nodes" \
                      .format(self.input.param("upgrade_version", "")))
        self.sleep(self.sleep_time)
        status, content = ClusterOperationHelper.find_orchestrator(self.master)
        self.assertTrue(status, msg="Unable to find orchestrator: {0}:{1}". \
                        format(status, content))
        FIND_MASTER = False
        for new_server in servers_in:
            if content.find(new_server.ip) >= 0:
                self._new_master(new_server)
                FIND_MASTER = True
                self.log.info("%s node %s becomes the master" \
                              % (self.input.param("upgrade_version", ""), new_server.ip))
                break
        if self.input.param("initial_version", "")[:5] in COUCHBASE_VERSION_2 \
                and not FIND_MASTER and not self.is_downgrade:
            raise Exception( \
                "After rebalance in {0} nodes, {0} node doesn't become master" \
                    .format(self.input.param("upgrade_version", "")))

        servers_out = self.servers[:self.nodes_init]
        self.log.info("Rebalanced out all old version nodes")
        self.cluster.rebalance(self.servers[:self.num_servers], [], servers_out)

    def online_upgrade_swap_rebalance(self):
        self._install(self.servers[:self.nodes_init])
        self.operations(self.servers[:self.nodes_init])
        self.initial_version = self.upgrade_versions[0]
        self.product = 'couchbase-server'
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
            self._install(self.servers[self.nodes_init:self.num_servers], community_to_enterprise=True)
        else:
            self._install(self.servers[self.nodes_init:self.num_servers])
        self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")
        self.swap_num_servers = self.input.param('swap_num_servers', 1)
        old_servers = self.servers[:self.nodes_init]
        new_vb_nums = RestHelper(RestConnection(self.master))._get_vbuckets(old_servers,
                                                                            bucket_name=self.buckets[0].name)
        new_servers = []
        for i in range(self.nodes_init / self.swap_num_servers):
            old_vb_nums = copy.deepcopy(new_vb_nums)
            servers_in = self.servers[(self.nodes_init + i * self.swap_num_servers):
            (self.nodes_init + (i + 1) * self.swap_num_servers)]
            servers_out = self.servers[(i * self.swap_num_servers):((i + 1) * self.swap_num_servers)]
            servers = old_servers + new_servers
            self.log.info("Swap rebalance: rebalance out %s old version nodes, rebalance in %s 2.0 Nodes"
                          % (self.swap_num_servers, self.swap_num_servers))
            self.cluster.rebalance(servers, servers_in, servers_out)
            self.sleep(self.sleep_time)
            old_servers = self.servers[((i + 1) * self.swap_num_servers):self.nodes_init]
            new_servers = new_servers + servers_in
            servers = old_servers + new_servers
            new_vb_nums = RestHelper(RestConnection(self.master))._get_vbuckets(servers,
                                                                                bucket_name=self.buckets[0].name)
            self._verify_vbucket_nums_for_swap(old_vb_nums, new_vb_nums)
            status, content = ClusterOperationHelper.find_orchestrator(servers[0])
            self.assertTrue(status, msg="Unable to find orchestrator: {0}:{1}". \
                            format(status, content))
            FIND_MASTER = False
            for new_server in new_servers:
                if content.find(new_server.ip) >= 0:
                    self._new_master(new_server)
                    FIND_MASTER = True
                    self.log.info("3.0 Node %s becomes the master" % (new_server.ip))
            if not FIND_MASTER and not self.is_downgrade:
                if self.input.param("initial_version", "")[:5] in COUCHBASE_VERSION_3 \
                        and self.input.param("upgrade_version", "")[:5] in SHERLOCK_VERSION:
                    raise Exception("After rebalance in {0} nodes, {0} node doesn't become" \
                                    " the master ".format(self.input.param("upgrade_version", "")[:5]))
                elif self.input.param("initial_version", "")[:5] in COUCHBASE_VERSION_2 \
                        and self.input.param("upgrade_version", "")[:5] in COUCHBASE_VERSION_3:
                    raise Exception("After rebalance in {0} nodes, {0} node doesn't become" \
                                    " the master ".format(self.input.param("upgrade_version", "")[:5]))

        """ verify DCP upgrade in 3.0.0 version """
        self.monitor_dcp_rebalance()
        self.add_built_in_server_user()
        self._create_ephemeral_buckets()
        self.verification(new_servers)

    def online_upgrade_add_services(self):
        half_node = len(self.servers) / 2
        self._install(self.servers[:2])
        self.operations(self.servers[:2])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. "
                                    " Wait for online upgrade to {0} version"
                   .format(self.initial_version))
        self.product = 'couchbase-server'
        self._install(self.servers[2:])
        self.sleep(self.sleep_time, "Installation of new version is done."
                                    " Wait for rebalance")
        self.log.info("Rebalanced in upgraded nodes and rebalanced out "
                      "nodes with old version")
        self.cluster.rebalance(self.servers, self.servers[2:],
                               self.servers[:2])
        self.sleep(10)

        """ verify DCP upgrade in 3.x.x version """
        self.master = self.servers[2]
        self.monitor_dcp_rebalance()
        self.sleep(self.sleep_time)
        try:
            for server in self.servers[2:]:
                if self.port and self.port != '8091':
                    server.port = self.port
            self._new_master(self.servers[2])
            self.verification(self.servers[2:])
            self.log.info("Upgrade nodes of old version")
            upgrade_threads = self._async_update(self.upgrade_versions[0],
                                                 self.servers[:2], None, True)
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed!")
            self.cluster.rebalance(self.servers[2:],
                                   self.servers[:2], [])
            self.log.info("Rebalanced in all new version nodes")
            self.sleep(self.sleep_time)
            self._create_ephemeral_buckets()
            self.verification(self.servers)
        finally:
            for server in self.servers:
                server.port = '8091'
        """ init_nodes=False so we could set service on it"""
        self.init_nodes = False
        self._install(self.servers[-1])
        self.cluster.rebalance(self.servers[:4],
                               self.servers[-1], [])
        # SecondaryIndexingScanTests().test_multi_create_query_explain_drop_index()
        # we could add more tests later

    def test_swap_rebalance_with_services(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        self.swap_num_servers = self.input.param('swap_num_servers', 4)
        # Install initial version on the specified nodes
        self._install(self.servers[:self.nodes_init])
        # Configure the nodes with services
        self.operations(self.servers[:self.nodes_init], services="kv,kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        # set the upgrade version
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        self.product = 'couchbase-server'
        # install the new version on different set of servers
        if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
            self._install(self.servers[self.nodes_init:self.num_servers], community_to_enterprise=True)
        else:
            self._install(self.servers[self.nodes_init:self.nodes_init + self.swap_num_servers])
        self.sleep(self.sleep_time, "Installation of new version is done. Wait for rebalance")
        if self.during_ops:
            for opn in self.during_ops:
                getattr(self, opn)()
        if self.wait_expire:
            self.sleep(self.expire_time)
            for bucket in self.buckets:
                bucket.kvs[1] = KVStore()
        # swap and rebalance the servers
        self.online_upgrade(services=["kv", "kv", "index", "n1ql"])
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql", servers=self.servers[self.nodes_init:self.num_servers])
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[self.nodes_init:self.num_servers])

        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]],
                                                         save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:self.nodes_init], [self.servers[self.nodes_init]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_regular_rebalance_with_services(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        self.swap_num_servers = self.input.param('swap_num_servers', 4)
        # Install initial version on the specified nodes
        self._install(self.servers[:self.nodes_init])
        # Configure the nodes with services
        self.operations(self.servers[:self.nodes_init], services="kv,kv,index,n1ql,kv,kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        # set the upgrade version
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))
        upgrade_servers = self.servers[self.swap_num_servers:self.nodes_init]
        upgrade_services = ["kv", "kv", "index", "n1ql"]
        # swap out half of the servers out and rebalance
        self.cluster.rebalance(self.servers, [], upgrade_servers)
        # install the new version on the swapped out servers
        self._install(self.servers[self.swap_num_servers:self.nodes_init])
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run during upgrade operations
        self.during_upgrade(self.servers[:self.nodes_init])
        # rebalance in the swapped out servers
        for i in range(0, len(upgrade_servers)):
            self.cluster.rebalance(self.servers, [upgrade_servers[i]], [], services=[upgrade_services[i]])

        self._new_master(self.servers[self.swap_num_servers])
        # swap out the remaining half of the servers and rebalance
        upgrade_servers = self.servers[:self.swap_num_servers]
        self.cluster.rebalance(self.servers, [], upgrade_servers)
        # install the new version on the swapped out servers
        self._install(self.servers[:self.swap_num_servers])
        for i in range(0, len(upgrade_servers)):
            self.cluster.rebalance(self.servers[self.swap_num_servers:self.nodes_init], [upgrade_servers[i]], [],
                                   services=[upgrade_services[i]])
        self.sleep(timeout=60)
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:self.nodes_init])

        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]],
                                                         save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:self.nodes_init], [self.servers[self.nodes_init]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_graceful_failover_with_services(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        self.swap_num_servers = self.input.param('swap_num_servers', 4)
        # Install initial version on the specified nodes
        self._install(self.servers[:self.nodes_init])
        # Configure the nodes with services
        self.operations(self.servers[:self.nodes_init], services="kv,index,n1ql,kv,kv,index,n1ql,kv")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        # set the upgrade version
        self.initial_version = self.upgrade_versions[0]
        self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for online upgrade to {0} version". \
                   format(self.initial_version))

        upgrade_servers = self.servers[self.swap_num_servers:self.nodes_init]
        # do a graceful failover of some nodes
        self.rest.fail_over('ns_1@' + upgrade_servers[0].ip, graceful=True)
        self.sleep(timeout=60)
        self.rest.fail_over('ns_1@' + upgrade_servers[1].ip)
        self.sleep(timeout=60)
        self.rest.fail_over('ns_1@' + upgrade_servers[2].ip)
        self.sleep(timeout=60)

        for i in range(0, len(upgrade_servers) - 1):
            # set recovery type of the failed over nodes
            self.rest.set_recovery_type('ns_1@' + upgrade_servers[i].ip, "full")

        # upgrade the failed over nodes with the new version
        upgrade_threads = self._async_update(self.upgrade_versions[0], upgrade_servers[:len(upgrade_servers) - 1])
        # wait upgrade statuses
        for upgrade_thread in upgrade_threads:
            upgrade_thread.join()
        success_upgrade = True
        while not self.queue.empty():
            success_upgrade &= self.queue.get()
        if not success_upgrade:
            self.fail("Upgrade failed. See logs above!")
        self.sleep(self.expire_time)

        # rebalance in the failed over nodes
        self.cluster.rebalance(self.servers[:self.nodes_init], [], [])

        # failover another node, this is done so that the conditions of graceful failover are met, otherwise
        # hard failover will be implemented
        self.rest.fail_over('ns_1@' + upgrade_servers[3].ip, graceful=True)
        self.sleep(timeout=60)
        # set recovery type of the failed over node
        self.rest.set_recovery_type('ns_1@' + upgrade_servers[3].ip, "full")
        self.sleep(timeout=60)

        # upgrade the failed over node with the new version
        upgrade_threads = self._async_update(self.upgrade_versions[0], [upgrade_servers[len(upgrade_servers) - 1]])
        # wait upgrade statuses
        for upgrade_thread in upgrade_threads:
            upgrade_thread.join()
        success_upgrade = True
        while not self.queue.empty():
            success_upgrade &= self.queue.get()
        if not success_upgrade:
            self.fail("Upgrade failed. See logs above!")
        self.sleep(self.expire_time)

        # rebalance in the failed over nodes
        self.cluster.rebalance(self.servers[:self.nodes_init], [], [])
        self._new_master(self.servers[self.swap_num_servers])
        upgrade_servers = self.servers[:self.swap_num_servers]
        # do a graceful failover of remaining nodes except 1
        self.rest.fail_over('ns_1@' + upgrade_servers[0].ip, graceful=True)
        self.sleep(timeout=60)
        self.rest.fail_over('ns_1@' + upgrade_servers[1].ip)
        self.sleep(timeout=60)
        self.rest.fail_over('ns_1@' + upgrade_servers[2].ip)
        self.sleep(timeout=60)

        for i in range(0, len(upgrade_servers) - 1):
            # set recovery type of the failed over nodes
            self.rest.set_recovery_type('ns_1@' + upgrade_servers[i].ip, "full")

        # upgrade the failed over nodes with the new version
        upgrade_threads = self._async_update(self.upgrade_versions[0], upgrade_servers[:len(upgrade_servers) - 1])
        # wait upgrade statuses
        for upgrade_thread in upgrade_threads:
            upgrade_thread.join()
        success_upgrade = True
        while not self.queue.empty():
            success_upgrade &= self.queue.get()
        if not success_upgrade:
            self.fail("Upgrade failed. See logs above!")
        self.sleep(self.expire_time)

        # rebalance in the failed over nodes
        self.cluster.rebalance(self.servers[:self.nodes_init], [], [])
        self.sleep(timeout=60)

        # failover another node, this is done so that the conditions of graceful failover are met, otherwise
        # hard failover will be implemented
        self.rest.fail_over('ns_1@' + upgrade_servers[3].ip, graceful=True)
        self.sleep(timeout=60)
        # set recovery type of the failed over node
        self.rest.set_recovery_type('ns_1@' + upgrade_servers[3].ip, "full")
        self.sleep(timeout=60)

        # upgrade the failed over node with the new version
        upgrade_threads = self._async_update(self.upgrade_versions[0], [upgrade_servers[len(upgrade_servers) - 1]])
        # wait upgrade statuses
        for upgrade_thread in upgrade_threads:
            upgrade_thread.join()
        success_upgrade = True
        while not self.queue.empty():
            success_upgrade &= self.queue.get()
        if not success_upgrade:
            self.fail("Upgrade failed. See logs above!")
        self.sleep(self.expire_time)
        # rebalance in the failed over node
        self.cluster.rebalance(self.servers[:self.nodes_init], [], [])
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:self.nodes_init])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]],
                                                         save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:self.nodes_init], [self.servers[self.nodes_init]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_xdcr_upgrade_with_services(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:self.nodes_init])
        # Configure the nodes with services on cluster1
        self.operations(self.servers[:self.nodes_init], services="kv,kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:self.nodes_init])
        if self.ddocs_num:
            self.create_ddocs_and_views()
        # set the upgrade version
        self.initial_version = self.upgrade_versions[0]
        # install new version on another set of nodes
        self._install(self.servers[self.nodes_init:self.num_servers])
        self.master = self.servers[self.nodes_init]
        # Configure the nodes with services on the other cluster2
        try:
            self.operations(self.servers[self.nodes_init:self.num_servers], services="kv,kv,index,n1ql")
            self.sleep(timeout=300)
        except Exception, ex:
            # do nothing, the bucket is created
            self.log.info("bucket is created")

        # create a xdcr relationship between cluster1 and cluster2
        rest_src = RestConnection(self.servers[0])
        rest_src.add_remote_cluster(self.servers[self.nodes_init].ip, self.servers[self.nodes_init].port,
                                    'Administrator', 'password', "C2")
        repl_id = rest_src.start_replication('continuous', 'default', "C2")
        if repl_id is not None:
            self.log.info("Replication created successfully")
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:self.nodes_init])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]],
                                                         save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(120)
                self.cluster.rebalance(self.servers[:self.nodes_init], [self.servers[self.nodes_init]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_with_services(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:self.nodes_init])
        # Configure the nodes with services on cluster
        self.operations(self.servers[:self.nodes_init], services="kv,index,n1ql,kv,kv,index,n1ql,kv")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:self.nodes_init])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        num_stoped_nodes = self.input.param('num_stoped_nodes', self.nodes_init)
        # upgrade all the nodes in the cluster offline
        upgrade_nodes = self.servers[self.nodes_init - num_stoped_nodes:self.nodes_init]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()
            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:self.nodes_init])
        self.check_seqno(seqno_expected)
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]],
                                                         save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:self.nodes_init], [self.servers[self.nodes_init]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_n1ql_service_in_new_version(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services on cluster
        self.operations(self.servers[:3], services="kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        upgrade_nodes = [self.servers[2]]
        # upgrade n1ql node to new version
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:3])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]],
                                                         save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[self.nodes_init]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:self.nodes_init], [self.servers[self.nodes_init]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_index_service_in_new_version(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services on cluster
        self.operations(self.servers[:3], services="kv,n1ql,index")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade index node to new version
        upgrade_nodes = [self.servers[2]]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:3])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:3], [self.servers[3]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_kv_service_in_new_version(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services
        self.operations(self.servers[:3], services="kv,n1ql,index")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade kv node to new version
        upgrade_nodes = [self.servers[0]]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:3])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:3], [self.servers[3]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_n1ql_index_service_in_new_version(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services
        self.operations(self.servers[:3], services="kv,n1ql,index")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade n1ql and index nodes to new version
        upgrade_nodes = self.servers[1:3]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:3])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:3], [self.servers[3]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_kv_index_service_in_new_version(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services
        self.operations(self.servers[:3], services="kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade kv and index nodes to new version
        upgrade_nodes = self.servers[0:2]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:3])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:3], [self.servers[3]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_kv_n1ql_service_in_new_version(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services
        self.operations(self.servers[:3], services="kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade kv and n1ql nodes to new version
        upgrade_nodes = [self.servers[0], self.servers[2]]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:3])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[3]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(self.expire_time)
                self.cluster.rebalance(self.servers[:3], [self.servers[3]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_n1ql_index_service_in_new_version_with_multiple_nodes(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        self.bucket_size = 1000
        # Install initial version on the specified nodes
        self._install(self.servers[:6])
        # Configure the nodes with services
        self.operations(self.servers[:6], services="kv,kv,n1ql,index,n1ql,index")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:6])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade index and n1ql nodes to new version when we have multiple nodes
        upgrade_nodes = self.servers[4:6]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            self.n1ql_server = self.get_nodes_from_services_map(
                service_type="n1ql")
            self.during_upgrade(self.servers[:6])
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:6])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[6]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[6]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(120)
                self.cluster.rebalance(self.servers[:6], [self.servers[6]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_kv_index_service_in_new_version_with_multiple_nodes(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        self.bucket_size = 1000
        # Install initial version on the specified nodes
        self._install(self.servers[:6])
        # Configure the nodes with services
        self.operations(self.servers[:6], services="kv,index,n1ql,n1ql,kv,index")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:6])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade index and kv nodes to new version when we have multiple nodes
        upgrade_nodes = self.servers[4:6]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            self.n1ql_server = self.get_nodes_from_services_map(
                service_type="n1ql")
            self.during_upgrade(self.servers[:6])
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:6])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[6]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[6]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(120)
                self.cluster.rebalance(self.servers[:6], [self.servers[6]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_kv_n1ql_service_in_new_version_with_multiple_nodes(self):
        after_upgrade_services_in = self.input.param("after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in", False)
        after_upgrade_buckets_out = self.input.param("after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param("after_upgrade_buckets_flush", False)

        self.bucket_size = 1000
        # Install initial version on the specified nodes
        self._install(self.servers[:6])
        # Configure the nodes with services
        self.operations(self.servers[:6], services="kv,index,n1ql,index,kv,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:6])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param('check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade n1ql and kv nodes to new version when we have multiple nodes
        upgrade_nodes = self.servers[4:6]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time, "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml', 'VERSION.txt,']:
                        output, error = remote.execute_command("rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip', 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes, save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version, upgrade_nodes)
            # wait upgrade statuses
            self.n1ql_server = self.get_nodes_from_services_map(
                service_type="n1ql")
            self.during_upgrade(self.servers[:6])
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Run the post_upgrade operations
        self._create_ephemeral_buckets()
        self.post_upgrade(self.servers[:6])
        # Add new services after the upgrade
        if after_upgrade_services_in is not False:
            for upgrade_version in self.upgrade_versions:
                if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[6]], save_upgrade_config=True)
                else:
                    upgrade_threads = self._async_update(upgrade_version, [self.servers[6]])

                for upgrade_thread in upgrade_threads:
                    upgrade_thread.join()
                success_upgrade = True
                while not self.queue.empty():
                    success_upgrade &= self.queue.get()
                if not success_upgrade:
                    self.fail("Upgrade failed. See logs above!")
                self.sleep(120)
                self.cluster.rebalance(self.servers[:6], [self.servers[6]], [],
                                       services=["kv", "index", "n1ql"])
        # creating new buckets after upgrade
        if after_upgrade_buckets_in is not False:
            self.bucket_size = 100
            self._create_sasl_buckets(self.master, 1)
            self._create_standard_buckets(self.master, 1)
            if self.ddocs_num:
                self.create_ddocs_and_views()
                gen_load = BlobGenerator('upgrade', 'upgrade-', self.value_size, end=self.num_items)
                self._load_all_buckets(self.master, gen_load, "create", self.expire_time, flag=self.item_flag)
        # deleting buckets after upgrade
        if after_upgrade_buckets_out is not False:
            self._all_buckets_delete(self.master)
        # flushing buckets after upgrade
        if after_upgrade_buckets_flush is not False:
            self._all_buckets_flush()

    def test_offline_upgrade_kv_index_with_index_swap_rebalance(self):
        after_upgrade_services_in = self.input.param(
            "after_upgrade_services_in", False)
        after_upgrade_buckets_in = self.input.param("after_upgrade_buckets_in",
                                                    False)
        after_upgrade_buckets_out = self.input.param(
            "after_upgrade_buckets_out", False)
        after_upgrade_buckets_flush = self.input.param(
            "after_upgrade_buckets_flush", False)

        # Install initial version on the specified nodes
        self._install(self.servers[:3])
        # Configure the nodes with services
        self.operations(self.servers[:3], services="kv,index,n1ql")
        # get the n1ql node which will be used in pre,during and post upgrade for running n1ql commands
        self.n1ql_server = self.get_nodes_from_services_map(
            service_type="n1ql")
        self.index_server = self.get_nodes_from_services_map(
            service_type="index")
        # Run the pre upgrade operations, typically creating index
        self.pre_upgrade(self.servers[:3])
        seqno_expected = 1
        if self.ddocs_num:
            self.create_ddocs_and_views()
            if self.input.param('run_index', False):
                self.verify_all_queries()
        if not self.initial_version.startswith("1.") and self.input.param(
                'check_seqno', True):
            self.check_seqno(seqno_expected)
        if self.during_ops:
            for opn in self.during_ops:
                if opn != 'add_back_failover':
                    getattr(self, opn)()
        # upgrade kv and index nodes to new version
        upgrade_nodes = self.servers[:3]
        for upgrade_version in self.upgrade_versions:
            self.sleep(self.sleep_time,
                       "Pre-setup of old version is done. Wait for upgrade to {0} version". \
                       format(upgrade_version))
            for server in upgrade_nodes:
                remote = RemoteMachineShellConnection(server)
                remote.stop_server()
                self.sleep(self.sleep_time)
                if self.wait_expire:
                    self.sleep(self.expire_time)
                if self.input.param('remove_manifest_files', False):
                    for file in ['manifest.txt', 'manifest.xml',
                                 'VERSION.txt,']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/{0}".format(file))
                        remote.log_command_output(output, error)
                if self.input.param('remove_config_files', False):
                    for file in ['config', 'couchbase-server.node', 'ip',
                                 'couchbase-server.cookie']:
                        output, error = remote.execute_command(
                            "rm -rf /opt/couchbase/var/lib/couchbase/{0}".format(
                                file))
                        remote.log_command_output(output, error)
                    self.buckets = []
                remote.disconnect()

            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version,
                                                     upgrade_nodes,
                                                     save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version,
                                                     upgrade_nodes)
            # wait upgrade statuses
            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)
        self.dcp_rebalance_in_offline_upgrade_from_version2()
        # Add new services after the upgrade
        self.log.info("Indexer node : {}".format(self.index_server))
        for upgrade_version in self.upgrade_versions:
            rest = RestConnection(self.master)
            versions = rest.get_nodes_versions()
            for version in versions:
                if "5" > version:
                    self.log.info("Atleast one of the nodes in the cluster is "
                                  "pre 5.0 version. Hence not performing "
                                  "swap rebalance of indexer node")
                    break
            if "5" > upgrade_version:
                break
            if self.initial_build_type == "community" and self.upgrade_build_type == "enterprise":
                upgrade_threads = self._async_update(upgrade_version,
                                                     [self.servers[3]],
                                                     save_upgrade_config=True)
            else:
                upgrade_threads = self._async_update(upgrade_version,
                                                     [self.servers[3]])

            for upgrade_thread in upgrade_threads:
                upgrade_thread.join()
            success_upgrade = True
            while not self.queue.empty():
                success_upgrade &= self.queue.get()
            if not success_upgrade:
                self.fail("Upgrade failed. See logs above!")
            self.sleep(self.expire_time)

            self.sleep(180, "Sleep for check")
            map_before_rebalance, stats_map_before_rebalance = \
                self._return_maps()
            index_node = self.get_nodes_from_services_map(service_type="index")
            to_add_nodes = [self.servers[3]]
            to_remove_nodes = [index_node]
            services_in = ["index"]
            rebalance = self.cluster.async_rebalance(
                self.servers[:self.nodes_init], to_add_nodes, [],
                services=services_in)
            reached = RestHelper(self.rest).rebalance_reached()
            self.assertTrue(reached,
                            "rebalance failed, stuck or did not complete")
            rebalance.result()
            rebalance = self.cluster.async_rebalance(
                self.servers[:self.nodes_init + 1], [], to_remove_nodes)
            index_server = self.get_nodes_from_services_map(
                service_type="index", get_all_nodes=False)
            for i in xrange(20):
                output = self.rest.list_indexer_rebalance_tokens(
                    server=index_server)
                if "rebalancetoken" in output:
                    self.log.info(output)
                    break
                self.sleep(2)
            if i == 19 and "rebalancetoken" not in output:
                self.log.warn(
                    "rebalancetoken was not returned by /listRebalanceTokens during gsi rebalance")
            reached = RestHelper(self.rest).rebalance_reached()
            self.assertTrue(reached,
                            "rebalance failed, stuck or did not complete")
            rebalance.result()
            self.sleep(30)
            map_after_rebalance, stats_map_after_rebalance = \
                self._return_maps()
            self.n1ql_helper.verify_indexes_redistributed(
                map_before_rebalance, map_after_rebalance,
                stats_map_before_rebalance, stats_map_after_rebalance,
                [self.servers[3]], [self.servers[1]], swap_rebalance=True)
        self.post_upgrade(self.servers[:3])