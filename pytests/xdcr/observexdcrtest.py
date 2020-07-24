from xdcrnewbasetests import XDCRNewBaseTest
from couchbase_helper.documentgenerator import BlobGenerator
from couchbase_helper.document import View
from observe.observetest import ObserveTests
from couchbase_helper.cluster import Cluster


# Assumption that at least 2 nodes on every cluster
class ObserveXdcrTest(XDCRNewBaseTest):
    def setUp(self):
        super(ObserveXdcrTest, self).setUp()
        self.src_cluster = self.get_cb_cluster_by_name('C1')
        self.src_main = self.src_cluster.get_main_node()
        self.dest_cluster = self.get_cb_cluster_by_name('C2')
        self.dest_main = self.dest_cluster.get_main_node()

        # Variables defined for _run_observe() in observetest.
        self.observe_with = self._input.param("observe_with", "")
        self.default_map_func = 'function (doc) { emit(doc.age, doc.first_name);}'
        self.default_design_doc = "Doc1"
        map_func = 'function (doc) { emit(null, doc);}'
        self.default_view = View("default_view", map_func, None)
        self.mutate_by = self._input.param("mutate_by", "set")
        self.cluster = Cluster()
        self.wait_timeout = self._wait_timeout
        self.num_items = self._num_items

    def tearDown(self):
        super(ObserveXdcrTest, self).tearDown()

    def observe_xdcr(self):
        self.set_xdcr_topology()
        self.setup_all_replications()
        gen_load = BlobGenerator('observe', 'observe', 1024, end=self.num_items)
        self.src_cluster.load_all_buckets_from_generator(gen_load)
        self.verify_results()
        self.main = self.src_main
        self.buckets = self.src_cluster.get_buckets()
        ObserveTests._run_observe(self)
        self.main = self.dest_main
        self.buckets = self.dest_cluster.get_buckets()
        ObserveTests._run_observe(self)
