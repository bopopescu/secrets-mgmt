from basetestcase import BaseTestCase
import json
import os
import zipfile
import pprint
import Queue
import json
from membase.helper.cluster_helper import ClusterOperationHelper
import mc_bin_client
import threading
from memcached.helper.data_helper import  VBucketAwareMemcached
from mysql_client import MySQLClient
from membase.api.rest_client import RestConnection, Bucket
from couchbase_helper.tuq_helper import N1QLHelper
from couchbase_helper.query_helper import QueryHelper

class RQGTests(BaseTestCase):
    """ Class for defining tests for RQG base testing """

    def setUp(self):
        super(RQGTests, self).setUp()
        self.log.info("==============  RQGTests setup was finished for test #{0} {1} =============="\
                      .format(self.case_number, self._testMethodName))
        self.use_mysql= self.input.param("use_mysql",True)
        self.initial_loading_to_cb= self.input.param("initial_loading_to_cb",True)
        self.database= self.input.param("database","flightstats")
        self.merge_operation= self.input.param("merge_operation",False)
        self.user_id= self.input.param("user_id","root")
        self.password= self.input.param("password","")
        self.generate_input_only = self.input.param("generate_input_only",False)
        self.using_gsi= self.input.param("using_gsi",True)
        self.reset_database = self.input.param("reset_database",True)
        self.create_secondary_indexes = self.input.param("create_secondary_indexes",False)
        self.items = self.input.param("items",1000)
        self.mysql_url= self.input.param("mysql_url","localhost")
        self.mysql_url=self.mysql_url.replace("_",".")
        self.gen_secondary_indexes= self.input.param("gen_secondary_indexes",False)
        self.gen_gsi_indexes= self.input.param("gen_gsi_indexes",True)
        self.n1ql_server = self.get_nodes_from_services_map(service_type = "n1ql")
        self.create_all_indexes= self.input.param("create_all_indexes",False)
        self.concurreny_count= self.input.param("concurreny_count",10)
        self.total_queries= self.input.param("total_queries",None)
        self.run_query_without_index_hint= self.input.param("run_query_without_index_hint",True)
        self.run_query_with_primary= self.input.param("run_query_with_primary",False)
        self.run_query_with_secondary= self.input.param("run_query_with_secondary",False)
        self.run_explain_with_hints= self.input.param("run_explain_with_hints",False)
        self.test_file_path= self.input.param("test_file_path",None)
        self.secondary_index_info_path= self.input.param("secondary_index_info_path",None)
        self.db_dump_path= self.input.param("db_dump_path",None)
        self.input_rqg_path= self.input.param("input_rqg_path",None)
        if self.input_rqg_path != None:
            self.secondary_index_info_path = self.input_rqg_path+"/index/secondary_index_definitions.txt"
            self.db_dump_path = self.input_rqg_path+"/db_dump/database_dump.zip"
            self.test_file_path = self.input_rqg_path+"/input/source_input_rqg_run.txt"
        self.query_helper = QueryHelper()
        self.keyword_list = self.query_helper._read_keywords_from_file("b/resources/rqg/n1ql_info/keywords.txt")
        self._initialize_n1ql_helper()
        if self.initial_loading_to_cb:
            self._initialize_cluster_setup()

    def tearDown(self):
        super(RQGTests, self).tearDown()
        if hasattr(self, 'reset_database'):
            self.skip_cleanup= self.input.param("skip_cleanup",False)
            if self.use_mysql and self.reset_database and (not self.skip_cleanup):
                self.client.drop_database(self.database)

    def test_rqg_example(self):
        self._initialize_mysql_client()
        sql_query = "SELECT a1.* FROM ( `ontime_mysiam`  AS a1 INNER JOIN `carriers`  AS a2 ON ( a1.`carrier` = a2.`code` ) )"
        n1ql_query = "SELECT a1.* FROM `ontime_mysiam`  AS a1 INNER JOIN `carriers`  AS a2 ON KEYS [ a1.`carrier` ]"
        # Run n1ql query
        check, msg = self._run_queries_compare(n1ql_query = n1ql_query , sql_query = sql_query)
        self.assertTrue(check, msg)

    def test_rqg_from_list(self):
        self._initialize_mysql_client()
        self.n1ql_file_path= self.input.param("n1ql_file_path","default")
        self.sql_file_path= self.input.param("sql_file_path","default")
        with open(self.n1ql_file_path) as f:
            n1ql_query_list = f.readlines()
        with open(self.sql_file_path) as f:
            sql_query_list = f.readlines()
        self._generate_secondary_indexes(n1ql_query_list)
        i = 0
        check = True
        pass_case = 0
        total =0
        fail_case = 0
        failure_map = {}
        self.assertTrue(len(n1ql_query_list) == len(sql_query_list),
         "number of query mismatch n1ql:{0}, sql:{1}".format(len(n1ql_query_list),len(sql_query_list)))
        for n1ql_query in n1ql_query_list:
            self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< BEGIN RUNNING QUERY CASE NUMBER {0} >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(i))
            sql_query = sql_query_list[i]
            i+=1
            # Run n1ql query
            success, msg = self._run_queries_compare(n1ql_query = n1ql_query , sql_query = sql_query)
            total += 1
            check = check and success
            if success:
                pass_case += 1
            else:
                fail_case +=  1
                failure_map["Case :: "+str(i-1)] = { "sql_query":sql_query, "n1ql_query": n1ql_query, "reason for failure": msg}
            self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< END RUNNING QUERY CASE NUMBER {0} >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(i-1))
        self.log.info(" Total Queries Run = {0}, Pass = {1}, Fail = {2}".format(total, pass_case, fail_case))
        self.assertTrue(check, failure_map)

    def test_rqg_concurrent_with_predefined_input(self):
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        with open(self.test_file_path) as f:
            n1ql_query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(n1ql_query_list)
        for n1ql_query_info in n1ql_query_list:
            data = json.loads(n1ql_query_info)
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(n1ql_query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            if self.use_secondary_index:
                self._generate_secondary_indexes_in_batches(list)
            thread_list = []
            # Create threads and run the batch
            for test_case in test_batch:
                test_case_number = test_case.keys()[0]
                data = test_case[test_case_number]
                t = threading.Thread(target=self._run_basic_test, args = (data, test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
            # Drop all the secondary Indexes
            if self.use_secondary_index:
                self._drop_secondary_indexes_in_batches(list)
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def test_rqg_concurrent_with_predefined_input(self):
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        with open(self.test_file_path) as f:
            n1ql_query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(n1ql_query_list)
        for n1ql_query_info in n1ql_query_list:
            data = json.loads(n1ql_query_info)
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(n1ql_query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            if self.use_secondary_index:
                self._generate_secondary_indexes_in_batches(list)
            thread_list = []
            # Create threads and run the batch
            for test_case in test_batch:
                test_case_number = test_case.keys()[0]
                data = test_case[test_case_number]
                t = threading.Thread(target=self._run_basic_test, args = (data, test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
            # Drop all the secondary Indexes
            if self.use_secondary_index:
                self._drop_secondary_indexes_in_batches(list)
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def test_rqg_generate_input(self):
        self.data_dump_path= self.input.param("data_dump_path","b/resources/rqg/data_dump")
        input_file_path=self.data_dump_path+"/input"
        os.mkdir(input_file_path)
        f_write_file = open(input_file_path+"/source_input_rqg_run.txt",'w')
        secondary_index_path=self.data_dump_path+"/index"
        os.mkdir(secondary_index_path)
        database_dump = self.data_dump_path+"/db_dump"
        os.mkdir(database_dump)
        f_write_index_file = open(secondary_index_path+"/secondary_index_definitions.txt",'w')
        self.client.dump_database(data_dump_path = database_dump)
        f_write_index_file.write(json.dumps(self.sec_index_map))
        f_write_index_file.close()
        # Get Data Map
        table_map = self.client._get_values_with_type_for_fields_in_table()
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        # Load All the templates
        self.test_file_path= self.unzip_template(self.test_file_path)
        with open(self.test_file_path) as f:
            query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(query_list)
        for n1ql_query_info in query_list:
            data = n1ql_query_info
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(query_list):
            batches.append(batch)
        # Run Test Batches
        test_case_number = 1
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            list = self.client._convert_template_query_info(
                    table_map = table_map,
                    n1ql_queries = list,
                    define_gsi_index = self.use_secondary_index,
                    gen_expected_result = True)
            # Create threads and run the batch
            for test_case in list:
                test_case_input = test_case
                data = self._generate_test_data(test_case_input)
                f_write_file.write(json.dumps(data)+"\n")
        f_write_file.close()

    def test_rqg_concurrent(self):
        # Get Data Map
        table_map = self.client._get_values_with_type_for_fields_in_table()
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        # Load All the templates
        self.test_file_path= self.unzip_template(self.test_file_path)
        with open(self.test_file_path) as f:
            query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(query_list)
        for n1ql_query_info in query_list:
            data = n1ql_query_info
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        # Run Test Batches
        test_case_number = 1
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            list = self.client._convert_template_query_info(
                    table_map = table_map,
                    n1ql_queries = list,
                    define_gsi_index = self.use_secondary_index,
                    gen_expected_result = True)
            if self.use_secondary_index:
                self._generate_secondary_indexes_in_batches(list)
            thread_list = []
            # Create threads and run the batch
            for test_case in list:
                test_case_input = test_case
                t = threading.Thread(target=self._run_basic_test, args = (test_case_input, test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
                test_case_number += 1
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
            # Drop all the secondary Indexes
            if self.use_secondary_index:
                self._drop_secondary_indexes_in_batches(list)
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def test_rqg_crud_update_merge(self):
        # Get Data Map
        #Create Table
        table_map = self.client._get_values_with_type_for_fields_in_table()
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        # Load All the templates
        self.test_file_path= self.unzip_template(self.test_file_path)
        with open(self.test_file_path) as f:
            query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(query_list)
        for n1ql_query_info in query_list:
            data = n1ql_query_info
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        # Run Test Batches
        test_case_number = 1
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            list = self.client._convert_update_template_query_info_with_merge(
                    source_table = "copy_simple_table",
                    target_table = "simple_table",
                    table_map = table_map,
                    n1ql_queries = list)
            thread_list = []
            # Create threads and run the batch
            for test_case in list:
                test_case_input = test_case
                verification_query = "SELECT * from {0} ORDER by primary_key_id".format(table_map.keys()[0])
                t = threading.Thread(target=self._run_basic_crud_test, args = (test_case_input, verification_query,  test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
                test_case_number += 1
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def test_rqg_crud_update(self):
        # Get Data Map
        table_map = self.client._get_values_with_type_for_fields_in_table()
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        # Load All the templates
        self.test_file_path= self.unzip_template(self.test_file_path)
        with open(self.test_file_path) as f:
            query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(query_list)
        for n1ql_query_info in query_list:
            data = n1ql_query_info
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        # Run Test Batches
        test_case_number = 1
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            list = self.client._convert_update_template_query_info(
                    table_map = table_map,
                    n1ql_queries = list)
            if self.use_secondary_index:
                self._generate_secondary_indexes_in_batches(list)
            thread_list = []
            # Create threads and run the batch
            for test_case in list:
                test_case_input = test_case
                verification_query = "SELECT * from {0} ORDER by primary_key_id".format(table_map.keys()[0])
                t = threading.Thread(target=self._run_basic_crud_test, args = (test_case_input, verification_query,  test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
                test_case_number += 1
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
            # Drop all the secondary Indexes
            if self.use_secondary_index:
                self._drop_secondary_indexes_in_batches(list)
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def test_rqg_crud_delete(self):
        # Get Data Map
        table_map = self.client._get_values_with_type_for_fields_in_table()
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        # Load All the templates
        self.test_file_path= self.unzip_template(self.test_file_path)
        with open(self.test_file_path) as f:
            query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(query_list)
        for n1ql_query_info in query_list:
            data = n1ql_query_info
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        # Run Test Batches
        test_case_number = 1
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            list = self.client._convert_delete_template_query_info(
                    table_map = table_map,
                    n1ql_queries = list)
            if self.use_secondary_index:
                self._generate_secondary_indexes_in_batches(list)
            thread_list = []
            # Create threads and run the batch
            for test_case in list:
                test_case_input = test_case
                verification_query = "SELECT * from {0} ORDER by primary_key_id".format(table_map.keys()[0])
                t = threading.Thread(target=self._run_basic_crud_test, args = (test_case_input, verification_query,  test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
                test_case_number += 1
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
            # Drop all the secondary Indexes
            self._populate_delta_buckets()
            if self.use_secondary_index:
                self._drop_secondary_indexes_in_batches(list)
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def test_rqg_crud_delete_merge(self):
        # Get Data Map
        table_map = self.client._get_values_with_type_for_fields_in_table()
        check = True
        failure_map = {}
        batches = []
        batch = []
        test_case_number = 1
        count = 1
        inserted_count = 0
        self.use_secondary_index = self.run_query_with_secondary or self.run_explain_with_hints
        # Load All the templates
        self.test_file_path= self.unzip_template(self.test_file_path)
        with open(self.test_file_path) as f:
            query_list = f.readlines()
        if self.total_queries  == None:
            self.total_queries = len(query_list)
        for n1ql_query_info in query_list:
            data = n1ql_query_info
            batch.append({str(test_case_number):data})
            if count == self.concurreny_count:
                inserted_count += len(batch)
                batches.append(batch)
                count = 1
                batch = []
            else:
                count +=1
            test_case_number += 1
            if test_case_number >= self.total_queries:
                break
        if inserted_count != len(query_list):
            batches.append(batch)
        result_queue = Queue.Queue()
        # Run Test Batches
        test_case_number = 1
        for test_batch in batches:
            # Build all required secondary Indexes
            list = [data[data.keys()[0]] for data in test_batch]
            list = self.client._convert_delete_template_query_info_with_merge(
                    table_map = table_map,
                    n1ql_queries = list)
            if self.use_secondary_index:
                self._generate_secondary_indexes_in_batches(list)
            thread_list = []
            # Create threads and run the batch
            for test_case in list:
                test_case_input = test_case
                verification_query = "SELECT * from {0} ORDER by primary_key_id".format(table_map.keys()[0])
                t = threading.Thread(target=self._run_basic_crud_test, args = (test_case_input, verification_query,  test_case_number, result_queue))
                t.daemon = True
                t.start()
                thread_list.append(t)
                test_case_number += 1
            # Capture the results when done
            check = False
            for t in thread_list:
                t.join()
            # Drop all the secondary Indexes
            self._populate_delta_buckets()
            if self.use_secondary_index:
                self._drop_secondary_indexes_in_batches(list)
        # Analyze the results for the failure and assert on the run
        success, summary, result = self._test_result_analysis(result_queue)
        self.log.info(result)
        self.assertTrue(success, summary)

    def _run_basic_test(self, test_data, test_case_number, result_queue):
        data = test_data
        n1ql_query = data["n1ql"]
        sql_query = data["sql"]
        indexes = data["indexes"]
        table_name = data["bucket"]
        expected_result = data["expected_result"]
        self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< BEGIN RUNNING TEST {0}  >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(test_case_number))
        result_run = {}
        result_run["n1ql_query"] = n1ql_query
        result_run["sql_query"] = sql_query
        result_run["test_case_number"] = test_case_number
        if  expected_result == None:
            expected_result = self._gen_expected_result(sql_query)
        query_index_run = self._run_queries_and_verify(n1ql_query = n1ql_query , sql_query = sql_query, expected_result = expected_result)
        result_run["run_query_without_index_hint"] = query_index_run
        if self.run_query_with_primary:
            index_info = {"name":"`#primary`","type":"GSI"}
            query = self.query_helper._add_index_hints_to_query(n1ql_query, [index_info])
            query_index_run = self._run_queries_and_verify(n1ql_query = query , sql_query = sql_query, expected_result = expected_result)
            result_run["run_query_with_primary"] = query_index_run
        if self.run_query_with_secondary:
            for index_name in indexes.keys():
                query = self.query_helper._add_index_hints_to_query(n1ql_query, [indexes[index_name]])
                query_index_run = self._run_queries_and_verify(n1ql_query = query , sql_query = sql_query, expected_result = expected_result)
                key = "run_query_with_index_name::{0}".format(index_name)
                result_run[key] = query_index_run
        if self.run_explain_with_hints:
            result = self._run_queries_with_explain(n1ql_query , indexes)
            result_run.update(result)
        result_queue.put(result_run)
        self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< END RUNNING TEST {0}  >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(test_case_number))

    def _generate_test_data(self, test_data):
        query_info_list = []
        data = test_data
        n1ql_query = data["n1ql"]
        sql_query = data["sql"]
        indexes = data["indexes"]
        table_name = data["bucket"]
        expected_result = data["expected_result"]
        if  expected_result == None:
            data["expected_result"] = self._gen_expected_result(sql_query)
        return data

    def _run_basic_crud_test(self, test_data, verification_query, test_case_number, result_queue):
        self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< BEGIN RUNNING TEST {0}  >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(test_case_number))
        result_run = {}
        n1ql_query = test_data["n1ql_query"]
        sql_query = test_data["sql_query"]
        result_run["n1ql_query"] = n1ql_query
        result_run["sql_query"] = sql_query
        result_run["test_case_number"] = test_case_number
        self.n1ql_helper.run_cbq_query(n1ql_query, self.n1ql_server)
        self.client._db_execute_query(query = sql_query)
        query_index_run = self._run_queries_and_verify(n1ql_query = verification_query , sql_query = verification_query, expected_result = None)
        result_run["update_test"] = query_index_run
        result_queue.put(result_run)
        self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< END RUNNING TEST {0}  >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(test_case_number))

    def _test_result_analysis(self, queue):
        result_list = []
        pass_case = 0
        fail_case = 0
        total= 0
        failure_map = {}
        keyword_map = {}
        failure_reason_map = {}
        success = True
        while not queue.empty():
            result_list.append(queue.get())
        for result_run in result_list:
            test_case_number = result_run["test_case_number"]
            sql_query = result_run["sql_query"]
            n1ql_query = result_run["n1ql_query"]
            check, message, failure_types = self._analyze_result(result_run)
            total += 1
            success = success and check
            if check:
                pass_case += 1
            else:
                fail_case +=  1
                for failure_reason_type in failure_types:
                    if failure_reason_type not in failure_reason_map.keys():
                        failure_reason_map[failure_reason_type] = 1
                    else:
                        failure_reason_map[failure_reason_type] += 1
                keyword_list = self.query_helper.find_matching_keywords(n1ql_query, self.keyword_list)
                for keyword in keyword_list:
                    if keyword not in keyword_map.keys():
                        keyword_map[keyword] = 1
                    else:
                        keyword_map[keyword] += 1
                failure_map[test_case_number] = {"sql_query":sql_query, "n1ql_query": n1ql_query,
                 "run_result" : message, "keyword_list": keyword_list}
        summary = " Total Queries Run = {0}, Pass = {1}, Fail = {2}".format(total, pass_case, fail_case)
        if len(keyword_map) > 0:
            summary += "\n [ KEYWORD FAILURE DISTRIBUTION ] \n"
        for keyword in keyword_map.keys():
            summary  += keyword+" :: " + str((keyword_map[keyword]*100)/total)+"%\n "
        if len(failure_reason_map)  > 0:
            summary += "\n [ FAILURE TYPE DISTRIBUTION ] \n"
            for keyword in failure_reason_map.keys():
                summary  += keyword+" :: " + str((failure_reason_map[keyword]*100)/total)+"%\n "
        self.log.info(" Total Queries Run = {0}, Pass = {1}, Fail = {2}".format(total, pass_case, fail_case))
        result = self._generate_result(failure_map)
        return success, summary, result

    def test_rqg_main(self):
        self.run_query_without_index_hint= self.input.param("run_query_without_index_hint",True)
        self.run_query_with_primary= self.input.param("run_query_with_primary",True)
        self.run_query_with_secondary= self.input.param("run_query_with_secondary",True)
        self.run_explain_with_hints= self.input.param("run_explain_with_hints",True)
        self.n1ql_file_path= self.input.param("test_file_path","default")
        with open(self.n1ql_file_path) as f:
            n1ql_query_list = f.readlines()
        i = 0
        check = True
        pass_case = 0
        total =0
        fail_case = 0
        failure_map = {}
        for n1ql_query_info in n1ql_query_list:
            # Run n1ql query
            data = json.loads(n1ql_query_info)
            n1ql_query = data["n1ql"]
            sql_query = data["sql"]
            indexes = data["indexes"]
            table_name = data["bucket"]
            expected_result = data["expected_result"]
            run_result ={}
            hints = self.query_helper._find_hints(n1ql_query)
            if self.run_query_with_secondary or self.run_explain_with_hints:
                self._generate_secondary_indexes_with_index_map(index_map = indexes, table_name = table_name)
            self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< BEGIN RUNNING TEST {0}  >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(total))
            result_run = {}
            if expected_result == None:
                expected_result = self._gen_expected_result(sql_query)
            if self.run_query_without_index_hint:
                query_index_run = self._run_queries_and_verify(n1ql_query = n1ql_query , sql_query = sql_query, expected_result = expected_result)
                result_run["run_query_without_index_hint"] = query_index_run
            if self.run_query_with_primary:
                index_info = {"name":"`#primary`","type":"GSI"}
                query = self.query_helper._add_index_hints_to_query(n1ql_query, [index_info])
                query_index_run = self._run_queries_and_verify(n1ql_query = query , sql_query = sql_query, expected_result = expected_result)
                result_run["run_query_with_primary"] = query_index_run
            if self.run_query_with_secondary:
                for index_name in indexes.keys():
                    query = self.query_helper._add_index_hints_to_query(n1ql_query, [indexes[index_name]])
                    query_index_run = self._run_queries_and_verify(n1ql_query = query , sql_query = sql_query, expected_result = expected_result)
                    key = "run_query_with_index_name::{0}".format(index_name)
                    result_run[key] = query_index_run
            if self.run_explain_with_hints:
                result = self._run_queries_with_explain(n1ql_query , indexes)
                result_run.update(result)
            message = "NO FAILURES \n"
            check, message = self._analyze_result(result_run)
            total += 1
            if check:
                pass_case += 1
            else:
                fail_case +=  1
                failure_map[str(total)] = {"sql_query":sql_query, "n1ql_query": n1ql_query,
                 "run_result" : message}
            if self.run_query_with_secondary or self.run_explain_with_hints:
                self._drop_secondary_indexes_with_index_map(index_map = indexes, table_name = table_name)
            self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< END RUNNING QUERY CASE NUMBER {0} >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(total))
        self.log.info(" Total Queries Run = {0}, Pass = {1}, Fail = {2}".format(total, pass_case, fail_case))
        result = self._generate_result(failure_map)
        self.assertTrue(fail_case == 0, result)

    def test_rqg_from_file(self):
        self.n1ql_file_path= self.input.param("n1ql_file_path","default")
        with open(self.n1ql_file_path) as f:
            n1ql_query_list = f.readlines()
        self._generate_secondary_indexes(n1ql_query_list)
        i = 0
        check = True
        pass_case = 0
        total =0
        fail_case = 0
        failure_map = {}
        for n1ql_query_info in n1ql_query_list:
            # Run n1ql query
            data = json.loads(n1ql_query_info)
            case_number = data["test case number"]
            n1ql_query = data["n1ql_query"]
            sql_query = data["sql_query"]
            expected_result = data["expected_result"]
            hints = self.query_helper._find_hints(n1ql_query)
            self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< BEGIN RUNNING QUERY  >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(case_number))

            success, msg = self._run_queries_from_file_and_compare(n1ql_query = n1ql_query , sql_query = sql_query, sql_result = expected_result)
            total += 1
            check = check and success
            if success:
                pass_case += 1
            else:
                fail_case +=  1
                failure_map[case_number] = { "sql_query":sql_query, "n1ql_query": n1ql_query, "reason for failure": msg}
            self.log.info(" <<<<<<<<<<<<<<<<<<<<<<<<<<<< END RUNNING QUERY CASE NUMBER {0} >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>".format(case_number))
        self.log.info(" Total Queries Run = {0}, Pass = {1}, Fail = {2}".format(total, pass_case, fail_case))
        self.assertTrue(check, failure_map)

    def test_n1ql_queries_only(self):
        self.n1ql_file_path= self.input.param("n1ql_file_path","default")
        with open(self.n1ql_file_path) as f:
            n1ql_query_list = f.readlines()
        self._generate_secondary_indexes(n1ql_query_list)
        failure_list = []
        n1ql_query_list = self.query_helper._convert_sql_list_to_n1ql(n1ql_query_list)
        check = True
        for n1ql_query in n1ql_query_list:
            try:
                self._run_n1ql_queries(n1ql_query = n1ql_query)
            except Exception, ex:
                self.log.info(ex)
                check = False
                failure_list.append({"n1ql_query":n1ql_query, "reason":ex})
        self.assertTrue(check, failure_list)

    def test_bootstrap_with_data(self):
        self.log.info(" Data has been bootstrapped !!")
        self.skip_cleanup=True

    def test_take_mysql_query_response_snap_shot(self):
        self._initialize_mysql_client()
        self.file_prefix= self.input.param("file_prefix","default_rqg_test")
        self.data_dump_path= self.input.param("data_dump_path","/tmp")
        self.queries_per_dump_file= self.input.param("queries_per_dump_file",10000)
        self.n1ql_file_path= self.input.param("n1ql_file_path","default")
        self.sql_file_path= self.input.param("sql_file_path","default")
        with open(self.n1ql_file_path) as f:
            n1ql_query_list = f.readlines()
        with open(self.sql_file_path) as f:
            sql_query_list = f.readlines()
        self._generate_secondary_indexes(n1ql_query_list)
        i = 0
        queries =0
        file_number=0
        f = open(self.data_dump_path+"/"+self.file_prefix+"_"+str(file_number)+".txt","w")
        for n1ql_query in n1ql_query_list:
            n1ql_query = n1ql_query.replace("\n","")
            sql_query = sql_query_list[i].replace("\n","")
            hints = self.query_helper._find_hints(n1ql_query)
            columns, rows = self.client._execute_query(query = sql_query)
            sql_result = self.client._gen_json_from_results(columns, rows)
            if hints == "FUN":
                sql_result = self._convert_fun_result(sql_result)
            dump_data = {
              "test case number":(i+1),
              "n1ql_query":n1ql_query,
              "sql_query":sql_query,
              "expected_result":sql_result
               }
            i+=1
            queries += 1
            f.write(json.dumps(dump_data)+"\n")
            if queries > self.queries_per_dump_file:
                queries = 0
                file_number = 1
                f.close()
                f = open(self.data_dump_path+"/"+self.file_prefix+"_"+file_number+".txt","w")
        f.close()

    def test_take_snapshot_of_database(self):
        self._take_snapshot_of_database()

    def test_load_data_of_database(self):
        self._setup_and_load_buckets_from_files()

    def _run_n1ql_queries(self, n1ql_query = None):
        # Run n1ql query
        actual_result = self.n1ql_helper.run_cbq_query(query = n1ql_query, server = self.n1ql_server)

    def _gen_expected_result(self, sql = ""):
        sql_result = []
        try:
            client = MySQLClient(database = self.database, host = self.mysql_url,
            user_id = self.user_id, password = self.password)
            columns, rows = client._execute_query(query = sql)
            sql_result = self.client._gen_json_from_results(columns, rows)
            client._close_mysql_connection()
            client = None
        except Exception, ex:
            self.log.info(ex)
        return sql_result

    def _run_no_change_queries_and_verify(self, n1ql_query = None, sql_query = None, expected_result = None):
        self.log.info(" SQL QUERY :: {0}".format(sql_query))
        self.log.info(" N1QL QUERY :: {0}".format(n1ql_query))
        result_run = {}
        # Run n1ql query
        try:
            actual_result = self.n1ql_helper.run_cbq_query(query = n1ql_query, server = self.n1ql_server)
            n1ql_result = actual_result["results"]
            #self.log.info(actual_result)
            # Run SQL Query
            sql_result = expected_result
            if expected_result == None:
                columns, rows = self.client._execute_query(query = sql_query)
                sql_result = self.client._gen_json_from_results(columns, rows)
            #self.log.info(sql_result)
            self.log.info(" result from n1ql query returns {0} items".format(len(n1ql_result)))
            self.log.info(" result from sql query returns {0} items".format(len(sql_result)))
            try:
                self.n1ql_helper._verify_results_rqg(sql_result = sql_result, n1ql_result = n1ql_result, hints = hints)
            except Exception, ex:
                self.log.info(ex)
                return {"success":False, "result": str(ex)}
            return {"success":True, "result": "Pass"}
        except Exception, ex:
            return {"success":False, "result": str(ex)}


    def _run_queries_and_verify(self, n1ql_query = None, sql_query = None, expected_result = None):
        self.log.info(" SQL QUERY :: {0}".format(sql_query))
        self.log.info(" N1QL QUERY :: {0}".format(n1ql_query))
        result_run = {}
        # Run n1ql query
        hints = self.query_helper._find_hints(sql_query)
        try:
            actual_result = self.n1ql_helper.run_cbq_query(query = n1ql_query, server = self.n1ql_server)
            n1ql_result = actual_result["results"]
            #self.log.info(actual_result)
            # Run SQL Query
            sql_result = expected_result
            if expected_result == None:
                columns, rows = self.client._execute_query(query = sql_query)
                sql_result = self.client._gen_json_from_results(columns, rows)
            #self.log.info(sql_result)
            self.log.info(" result from n1ql query returns {0} items".format(len(n1ql_result)))
            self.log.info(" result from sql query returns {0} items".format(len(sql_result)))
            try:
                self.n1ql_helper._verify_results_rqg(sql_result = sql_result, n1ql_result = n1ql_result, hints = hints)
            except Exception, ex:
                self.log.info(ex)
                return {"success":False, "result": str(ex)}
            return {"success":True, "result": "Pass"}
        except Exception, ex:
            return {"success":False, "result": str(ex)}

    def _run_queries_compare(self, n1ql_query = None, sql_query = None, expected_result = None):
        self.log.info(" SQL QUERY :: {0}".format(sql_query))
        self.log.info(" N1QL QUERY :: {0}".format(n1ql_query))
        # Run n1ql query
        hints = self.query_helper._find_hints(n1ql_query)
        try:
            actual_result = self.n1ql_helper.run_cbq_query(query = n1ql_query, server = self.n1ql_server)
            n1ql_result = actual_result["results"]
            #self.log.info(actual_result)
            # Run SQL Query
            sql_result = expected_result
            if expected_result == None:
                columns, rows = self.client._execute_query(query = sql_query)
                sql_result = self.client._gen_json_from_results(columns, rows)
            #self.log.info(sql_result)
            self.log.info(" result from n1ql query returns {0} items".format(len(n1ql_result)))
            self.log.info(" result from sql query returns {0} items".format(len(sql_result)))
            try:
                self.n1ql_helper._verify_results_rqg(sql_result = sql_result, n1ql_result = n1ql_result, hints = hints)
            except Exception, ex:
                self.log.info(ex)
                return False, ex
            return True, "Pass"
        except Exception, ex:
            return False, ex

    def _run_explain_and_print_result(self, n1ql_query):
        explain_query = "EXPLAIN "+n1ql_query
        try:
            actual_result = self.n1ql_helper.run_cbq_query(query = explain_query, server = self.n1ql_server)
            self.log.info(explain_query)
        except Exception, ex:
            self.log.info(ex)

    def _run_queries_with_explain(self, n1ql_query = None, indexes = {}):
        run_result = {}
        # Run n1ql query
        for index_name in indexes:
            hint = "USE INDEX({0} USING {1})".format(index_name,indexes[index_name]["type"])
            n1ql = self.query_helper._add_explain_with_hints(n1ql_query, hint)
            self.log.info(n1ql_query)
            message = "Pass"
            check = True
            try:
                actual_result = self.n1ql_helper.run_cbq_query(query = n1ql, server = self.n1ql_server)
                self.log.info(actual_result)
                check = self.n1ql_helper.verify_index_with_explain(actual_result, index_name)
                if not check:
                    message= " query {0} failed explain result, index {1} not found".format(n1ql_query,index_name)
                    self.log.info(message)
            except Exception, ex:
                self.log.info(ex)
                message = ex
                check = False
            finally:
                key = "Explain for index {0}".format(index_name)
                run_result[key] = {"success":check, "result":message}
        return run_result

    def _run_queries_from_file_and_compare(self, n1ql_query = None, sql_query = None, sql_result = None):
        self.log.info(" SQL QUERY :: {0}".format(sql_query))
        self.log.info(" N1QL QUERY :: {0}".format(n1ql_query))
        # Run n1ql query
        hints = self.query_helper._find_hints(n1ql_query)
        actual_result = self.n1ql_helper.run_cbq_query(query = n1ql_query, server = self.n1ql_server)
        n1ql_result = actual_result["results"]
        self.log.info(actual_result)
        self.log.info(sql_result)
        self.log.info(" result from n1ql query returns {0} items".format(len(n1ql_result)))
        self.log.info(" result from sql query returns {0} items".format(len(sql_result)))
        try:
            self.n1ql_helper._verify_results_rqg(sql_result = sql_result, n1ql_result = n1ql_result, hints = hints)
        except Exception, ex:
            self.log.info(ex)
            return False, ex
        return True, "Pass"

    def _initialize_cluster_setup(self):
        if self.use_mysql:
            self.log.info(" Will load directly from mysql")
            self._initialize_mysql_client()
            if not self.generate_input_only:
                self._setup_and_load_buckets()
        else:
            self.log.info(" Will load directly from file snap-shot")
            self._setup_and_load_buckets_from_files()
        self._initialize_n1ql_helper()
        #create copy of simple table if this is a merge operation
        self.sleep(10)
        self._build_indexes()

    def _build_indexes(self):
        if self.create_secondary_indexes:
            if self.use_mysql:
                self.sec_index_map  = self.client._gen_index_combinations_for_tables()
            else:
                self.sec_index_map  = self._extract_secondary_index_map_from_file(self.secondary_index_info_path)
        if not self.generate_input_only:
            if self.create_secondary_indexes:
                self._generate_secondary_indexes_during_initialize(self.sec_index_map)
            self._build_primary_indexes(self.using_gsi)

    def _build_primary_indexes(self, using_gsi= True):
        self.n1ql_helper.create_primary_index(using_gsi = using_gsi, server = self.n1ql_server)

    def _load_data_in_buckets_using_mc_bin_client(self, bucket, data_set):
        client = VBucketAwareMemcached(RestConnection(self.master), bucket)
        try:
            for key in data_set.keys():
                o, c, d = client.set(key, 0, 0, json.dumps(data_set[key]))
        except Exception, ex:
            print 'WARN======================='
            print ex

    def _load_data_in_buckets_using_n1ql(self, bucket, data_set):
        try:
            count=0
            for key in data_set.keys():
                if count%2 == 0:
                    n1ql_query = self.query_helper._insert_statement_n1ql(bucket.name, "\""+key+"\"", json.dumps(data_set[key]))
                else:
                    n1ql_query = self.query_helper._upsert_statement_n1ql(bucket.name, "\""+key+"\"", json.dumps(data_set[key]))
                actual_result = self.n1ql_helper.run_cbq_query(query = n1ql_query, server = self.n1ql_server)
                count+=1
        except Exception, ex:
            print 'WARN======================='
            print ex

    def _load_data_in_buckets_using_mc_bin_client_json(self, bucket, data_set):
        client = VBucketAwareMemcached(RestConnection(self.master), bucket)
        try:
            for key in data_set.keys():
                o, c, d = client.set(key.encode("utf8"), 0, 0, json.dumps(data_set[key]))
        except Exception, ex:
            print 'WARN======================='
            print ex

    def _load_data_in_buckets(self, bucket_name, data_set):
        from sdk_client import SDKClient
        scheme = "couchbase"
        host=self.master.ip
        if self.master.ip == "127.0.0.1":
            scheme = "http"
            host="{0}:{1}".format(self.master.ip,self.master.port)
        client = SDKClient(scheme=scheme,hosts = [host], bucket = bucket_name)
        client.upsert_multi(data_set)
        client.close()

    def _initialize_n1ql_helper(self):
        self.n1ql_helper = N1QLHelper(version = "sherlock", shell = None,
            use_rest = True, max_verify = self.max_verify,
            buckets = self.buckets, item_flag = None,
            n1ql_port = self.n1ql_server.n1ql_port, full_docs_list = [],
            log = self.log, input = self.input, master = self.master)

    def _initialize_mysql_client(self):
        if self.reset_database:
            self.client = MySQLClient(host = self.mysql_url,
                user_id = self.user_id, password = self.password)
            path  = "b/resources/rqg/{0}/database_definition/definition.sql".format(self.database)
            self.database = self.database+"_"+str(self.query_helper._random_int())
            self.client.reset_database_add_data(database = self.database, items= self.items,
             sql_file_definiton_path = path)
            self._copy_table_for_merge()
        else:
            self.client = MySQLClient(database = self.database, host = self.mysql_url,
                user_id = self.user_id, password = self.password)

    def _copy_table_for_merge(self):
        if self.merge_operation:
            path  = "b/resources/rqg/simple_table_db/database_definition/table_definition.sql"
            self.client.database_add_data(database = self.database, items= self.items,
             sql_file_definiton_path = path)
            sql = "INSERT INTO {0}.copy_simple_table SELECT * FROM {0}.simple_table".format(self.database)
            self.client._insert_execute_query(sql)

    def _zipdir(self, path, zip_path):
        self.log.info(zip_path)
        zipf = zipfile.ZipFile(zip_path, 'w')
        for root, dirs, files in os.walk(path):
            for file in files:
                zipf.write(os.path.join(root, file))

    def _calculate_secondary_indexing_information(self, query_list = []):
        secondary_index_table_map = {}
        table_field_map = self.client._get_field_list_map_for_tables()
        for table_name in table_field_map.keys():
            field_list = table_field_map[table_name]
            secondary_index_list = set([])
            for query in query_list:
                tokens = query.split(" ")
                check_for_table_name = False
                check_for_as = False
                table_name_alias = None
                for token in tokens:
                    if (not check_for_table_name) and (token == table_name):
                        check = True
                    if (not check_for_as) and check_for_table_name and (token == "AS" or token == "as"):
                        check_for_table_name = True
                    if check_for_table_name and token != " ":
                        table_name_alias  = token
                if table_name in query:
                    list = []
                    for field in table_field_map[table_name]:
                        field_name = field
                        if table_name_alias:
                            field_name = table_name_alias+"."+field_name
                        if field_name in query:
                            list.append(field)
                    if len(list) > 0:
                        secondary_index_list = set(secondary_index_list).union(set(list))
            list = []
            index_map ={}
            if len(secondary_index_list) > 0:
                list = [element for element in secondary_index_list]
                index_name = "{0}_{1}".format(table_name,"_".join(list))
                index_map = {index_name:list}
            for field in list:
                index_name = "{0}_{1}".format(table_name,field)
                index_map[index_name] = [field]
            if len(index_map) > 0:
                secondary_index_table_map[table_name] = index_map
        return secondary_index_table_map

    def _generate_result(self, data):
        result = ""
        for key in data.keys():
            result +="<<<<<<<<<< TEST {0} >>>>>>>>>>> \n".format(key)
            for result_key in data[key].keys():
                result += "{0} :: {1} \n".format(result_key, data[key][result_key])
        return result

    def _generate_secondary_indexes(self, query_list):
        if not self.gen_secondary_indexes:
            return
        secondary_index_table_map = self._calculate_secondary_indexing_information(query_list)
        for table_name in secondary_index_table_map.keys():
            self.log.info(" Building Secondary Indexes for Bucket {0}".format(table_name))
            for index_name in secondary_index_table_map[table_name].keys():
                query = "Create Index {0} on {1}({2}) ".format(index_name, table_name,
                    ",".join(secondary_index_table_map[table_name][index_name]))
                if self.gen_gsi_indexes:
                    query += " using gsi"
                self.log.info(" Running Query {0} ".format(query))
                try:
                    actual_result = self.n1ql_helper.run_cbq_query(query = query, server = self.n1ql_server)
                    check = self.n1ql_helper.is_index_online_and_in_list(table_name, index_name,
                        server = self.n1ql_server, timeout = 240)
                except Exception, ex:
                    self.log.info(ex)
                    raise

    def _generate_secondary_indexes_during_initialize(self, index_map = {}):
        if self.generate_input_only:
            return
        defer_mode = str({"defer_build":True})
        for table_name in index_map.keys():
            build_index_list = []
            batch_index_definitions = {}
            batch_index_definitions = index_map[table_name]
            for index_name in batch_index_definitions.keys():
                query = "{0} WITH {1}".format(
                    batch_index_definitions[index_name]["definition"],
                    defer_mode)
                build_index_list.append(index_name)
                self.log.info(" Running Query {0} ".format(query))
                try:
                    actual_result = self.n1ql_helper.run_cbq_query(query = query, server = self.n1ql_server)
                    build_index_list.append(index_name)
                except Exception, ex:
                    self.log.info(ex)
                    raise
            # Run Build Query
            if build_index_list != None and len(build_index_list) > 0:
                try:
                    build_query = "BUILD INDEX on {0}({1}) USING GSI".format(table_name,",".join(build_index_list))
                    actual_result = self.n1ql_helper.run_cbq_query(query = build_query, server = self.n1ql_server)
                    self.log.info(actual_result)
                except Exception, ex:
                    self.log.info(ex)
                    raise
                # Monitor till the index is built
                tasks = []
                try:
                    for index_name in build_index_list:
                        tasks.append(self.async_monitor_index(bucket = table_name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

    def _extract_secondary_index_map_from_file(self, file_path= "/tmp/index.txt"):
        with open(file_path) as data_file:
            return json.load(data_file)

    def _generate_secondary_indexes_in_batches(self, batches):
        if self.generate_input_only:
            return
        defer_mode = str({"defer_build":True})
        batch_index_definitions = {}
        build_index_list = []
        for info in batches:
            table_name = info["bucket"]
            batch_index_definitions.update(info["indexes"])
        for index_name in batch_index_definitions.keys():
            fail_index_name = index_name
            query = "{0} WITH {1}".format(
                batch_index_definitions[index_name]["definition"],
                defer_mode)
            self.log.info(" Running Query {0} ".format(query))
            try:
                actual_result = self.n1ql_helper.run_cbq_query(query = query, server = self.n1ql_server)
                build_index_list.append(index_name)
            except Exception, ex:
                self.log.info(ex)
                raise
        # Run Build Query
        if build_index_list != None and len(build_index_list) > 0:
            try:
                build_query = "BUILD INDEX on {0}({1}) USING GSI".format(table_name,",".join(build_index_list))
                actual_result = self.n1ql_helper.run_cbq_query(query = build_query, server = self.n1ql_server)
                self.log.info(actual_result)
            except Exception, ex:
                self.log.info(ex)
                raise
            # Monitor till the index is built
            tasks = []
            try:
                for info in batches:
                    table_name = info["bucket"]
                    for index_name in info["indexes"]:
                        if index_name in build_index_list:
                            tasks.append(self.async_monitor_index(bucket = table_name, index_name = index_name))
                for task in tasks:
                    task.result()
            except Exception, ex:
                self.log.info(ex)

    def async_monitor_index(self, bucket, index_name = None):
        monitor_index_task = self.cluster.async_monitor_index(
                 server = self.n1ql_server, bucket = bucket,
                 n1ql_helper = self.n1ql_helper,
                 index_name = index_name)
        return monitor_index_task

    def _drop_secondary_indexes_in_batches(self, batches):
        for info in batches:
            table_name = info["bucket"]
            for index_name in info["indexes"].keys():
                query ="DROP INDEX {0}.{1} USING {2}".format(table_name, index_name, info["indexes"][index_name]["type"])
                try:
                    self.n1ql_helper.run_cbq_query(query = query, server = self.n1ql_server)
                except Exception, ex:
                    self.log.info(ex)

    def _drop_secondary_indexes_with_index_map(self, index_map = {}, table_name = "simple_table"):
        self.log.info(" Dropping Secondary Indexes for Bucket {0}".format(table_name))
        for index_name in index_map.keys():
            query ="DROP INDEX {0}.{1} USING {2}".format(table_name, index_name, index_map[index_name]["type"])
            try:
                self.n1ql_helper.run_cbq_query(query = query, server = self.n1ql_server)
            except Exception, ex:
                self.log.info(ex)
                raise

    def _analyze_result(self, result):
        check = True
        failure_types = []
        message = "\n ____________________________________________________\n "
        for key in result.keys():
            if key != "test_case_number" and key != "n1ql_query" and key != "sql_query":
                check = check and result[key]["success"]
                if not result[key]["success"]:
                    failure_types.append(key)
                    message += " Scenario ::  {0} \n".format(key)
                    message += " Reason :: "+result[key]["result"]+"\n"
        return check, message, failure_types

    def _check_for_failcase(self, result):
        check=True
        for key in result.keys():
            if key != "test_case_number" and key != "n1ql_query" and key != "sql_query":
                check = check and result[key]["success"]
        return check

    def _convert_fun_result(self, result_set):
        list = []
        for data in result_set:
            map = {}
            for key in data.keys():
                val = data[key]
                if val == None:
                    val =0
                if not isinstance(val, int):
                    val = str(val)
                    if val == "":
                        val = 0
                map[key] =  val
            list.append(map)
        return list

    def unzip_template(self, template_path):
        if "zip" not in template_path:
            return template_path
        tokens =  template_path.split("/")
        file_name = tokens[len(tokens)-1]
        output_path = template_path.replace(file_name,"")
        with zipfile.ZipFile(template_path, "r") as z:
            z.extractall(output_path)
        template_path = template_path.replace(".zip","")
        return template_path

    def _setup_and_load_buckets_from_files(self):
        bucket_list =[]
        import shutil
        #Unzip the files and get bucket list
        tokens = self.db_dump_path.split("/")
        data_file_path = self.db_dump_path.replace(tokens[len(tokens)-1],"data_dump")
        os.mkdir(data_file_path)
        with zipfile.ZipFile(self.db_dump_path, "r") as z:
            z.extractall(data_file_path)
        from os import listdir
        from os.path import isfile, join
        onlyfiles = [ f for f in listdir(data_file_path) if isfile(join(data_file_path,f))]
        for file in onlyfiles:
            bucket_list.append(file.split(".")[0])
        # Remove any previous buckets
        rest = RestConnection(self.master)
        for bucket in self.buckets:
            rest.delete_bucket(bucket.name)
        self.buckets = []
        # Create New Buckets
        self._create_buckets(self.master, bucket_list, server_id=None, bucket_size=None)
        # Wait till the buckets are up
        self.sleep(15)
        # Read Data from mysql database and populate the couchbase server
        for bucket_name in bucket_list:
             for bucket in self.buckets:
                if bucket.name == bucket_name:
                    file_path = data_file_path+"/"+bucket_name+".txt"
                    with open(file_path) as data_file:
                        data = json.load(data_file)
                        self._load_data_in_buckets_using_mc_bin_client_json(bucket, data)
        shutil.rmtree(data_file_path, ignore_errors=True)

    def _setup_and_load_buckets(self):
        # Remove any previous buckets
        rest = RestConnection(self.master)
        for bucket in self.buckets:
            rest.delete_bucket(bucket.name)
        self.buckets = []
        # Pull information about tables from mysql database and interpret them as no-sql dbs
        table_key_map = self.client._get_primary_key_map_for_tables()
        # Make a list of buckets that we want to create for querying
        bucket_list = table_key_map.keys()
        # Create New Buckets
        self._create_buckets(self.master, bucket_list, server_id=None, bucket_size=None)
        # Wait till the buckets are up
        self.sleep(15)
        self.record_db = {}
        # Read Data from mysql database and populate the couchbase server
        for bucket_name in bucket_list:
            query = "select * from {0}".format(bucket_name)
            columns, rows = self.client._execute_query(query = query)
            self.record_db[bucket_name] = self.client._gen_json_from_results_with_primary_key(columns, rows,
                primary_key = table_key_map[bucket_name])
            for bucket in self.buckets:
                if bucket.name == bucket_name:
                    self._load_data_in_buckets_using_n1ql(bucket, self.record_db[bucket_name])

    def _populate_delta_buckets(self):
        table_key_map = self.client._get_primary_key_map_for_tables()
        bucket_list = table_key_map.keys()
        self.new_record_db={}
        for bucket_name in bucket_list:
            query = "select * from {0}".format(bucket_name)
            columns, rows = self.client._execute_query(query = query)
            new_record_db = self.client._gen_json_from_results_with_primary_key(columns, rows,
                primary_key = table_key_map[bucket_name])
            new_db_info ={}
            for key in self.record_db[bucket_name].keys():
                if key not in new_record_db.keys():
                    new_db_info[key]=self.record_db[bucket_name][key]
            #INSERT DATA AGAIN IN COUCHBASE
            for bucket in self.buckets:
                if bucket.name == bucket_name:
                    self._load_data_in_buckets_using_n1ql(bucket, new_db_info)
                    #INSERT DATA AGAIN IN MYSQL
                    for key in new_db_info.keys():
                        insert_sql = self.query_helper._generate_insert_statement_from_data(bucket_name,new_db_info[key])
                        self.client._insert_execute_query(insert_sql)





