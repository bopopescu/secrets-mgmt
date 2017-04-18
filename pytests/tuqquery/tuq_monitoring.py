import logging
import threading
import json
import uuid
import time

from newtuq import QueryTests
from membase.api.rest_client import RestConnection
from membase.api.exception import CBQError, ReadDocumentException
from remote.remote_util import RemoteMachineShellConnection



class QueryMonitoringTests(QueryTests):
    def setUp(self):
        super(QueryMonitoringTests, self).setUp()
        self.named_prepare = self.input.param("named_prepare", None)
        self.encoded_prepare = self.input.param("encoded_prepare", False)
        self.cover = self.input.param("cover", False)
        self.threadFailure = False
        self.run_cbq_query('delete from system:completed_requests')
        self.run_cbq_query('delete from system:prepareds')
        self.rest = RestConnection(self.master)
        self.rest.set_completed_requests_collection_duration(self.master, 1000)
        self.rest.set_completed_requests_max_entries(self.master, 4000)

    def suite_setUp(self):
        super(QueryMonitoringTests, self).suite_setUp()

    def tearDown(self):
        super(QueryMonitoringTests, self).tearDown()

    def suite_tearDown(self):
        super(QueryMonitoringTests, self).suite_tearDown()

##############################################################################################
#
#   Monitoring Test Cases (Normal Queries)
#
##############################################################################################

    '''Runs the basic cluster monitoring checks: (2 queries will be run when calling this method, each query will be
                                                  called from a different node)
            -check if the running queries are in system:active_requests
            -check if the queries' node fields accurately reflect the node they were started from
            -check if a query can be accessed from system:active_requests using its requestId
            -check if a query can be killed from system:active_requests using its requestId
                -once the query is killed check if it is in system:completed_requests
            -check if the queries appear in system:completed_requests when they complete.'''
    def test_simple_cluster_monitoring(self):
        self.test_monitoring(test='simple')

    '''Runs basic completed_requests deletions
            -check if you can delete the whole log
            -check if you can delete by node
            -check if you can delete by requestId'''
    def test_purge_completed(self):
        self.test_monitoring(test='purge')

    '''Checks to see if active_requests and completed_requests can be filtered by node'''
    def test_filter_by_node(self):
        self.test_monitoring(test='filter')

    '''Checks to see if the queries run from a server that has been downed are removed from system:completed_requests'''
    def test_server_failure(self):
        self.test_monitoring(test='simple')

        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 3)

        remote = RemoteMachineShellConnection(self.servers[1])
        remote.stop_server()

        time.sleep(30)
        #Check to see that completed_requests does not contain info from the downed node
        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 2)
        result = self.run_cbq_query('select * from system:completed_requests where node = "%s:%s"'
                                    %(self.servers[1].ip,self.servers[1].port))
        self.assertTrue(result['metrics']['resultCount'] == 0)

        #The info from the down node should not have been restored by the node coming back online
        remote.start_server()
        time.sleep(30)
        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 2)
        result = self.run_cbq_query('select * from system:completed_requests where node = "%s:%s"'
                                    % (self.servers[1].ip,self.servers[1].port))
        self.assertTrue(result['metrics']['resultCount'] == 0)

##############################################################################################
#
#   Monitoring Helper Functions
#
##############################################################################################

    def run_parallel_query(self, server):
        logging.info('parallel query is active')
        query = 'select * from default'
        self.run_cbq_query(query, server=server)

    '''Run basic cluster monitoring checks (outlined in the helper function) by executing 2 queries in parallel, must be
       run with a sufficient number of docs to be an effective test (docs-per-day >=3).'''
    def test_monitoring(self, test):
        for bucket in self.buckets:
            logging.info('PURGING COMPLETED REQUEST LOG')
            self.run_cbq_query('delete from system:completed_requests')
            result = self.run_cbq_query('select * from system:completed_requests')
            self.assertTrue(result['metrics']['resultCount'] == 0)
            logging.info("CHECKING THAT NO REQUESTS ARE RUNNING")
            result = self.run_cbq_query('select * from system:active_requests')
            self.assertTrue(result['metrics']['resultCount'] == 1)
            e = threading.Event()
            if test == 'simple':
                t50 = threading.Thread(name='run_simple_monitoring', target=self.run_simple_monitoring_check,
                                       args=(e, 2))
            elif test == 'purge':
                t50 = threading.Thread(name='run_purge', target=self.run_purge_completed_requests,
                                       args=(e, 2))
            elif test == 'filter':
                t50 = threading.Thread(name='run_filter_by_node', target=self.run_filter_by_node,
                                       args=(e, 2))
                t52 = threading.Thread(name='run_third_query', target=self.run_parallel_query,
                                       args=[self.servers[1]])
                t53 = threading.Thread(name='run_fourth_query', target=self.run_parallel_query,
                                       args=[self.servers[1]])
            t51 = threading.Thread(name='run_second_query', target=self.run_parallel_query,
                                   args=[self.servers[2]])
            t50.start()
            t51.start()
            if test == 'filter':
                t52.start()
                t53.start()
            e.set()
            query = 'select * from %s' % bucket.name
            self.run_cbq_query(query, server=self.servers[1])
            logging.debug('event is set')
            t50.join(100)
            t51.join(100)
            if test == 'filter':
                t52.join(100)
                t53.join(100)
            self.assertFalse(self.threadFailure)
            query_template = 'FROM %s select $str0, $str1 ORDER BY $str0,$str1 ASC' % bucket.name
            actual_result, expected_result = self.run_query_from_template(query_template)
            self._verify_results(actual_result['results'], expected_result)

    def run_simple_monitoring_check(self, e, t):
        while not e.isSet():
            logging.debug('wait_for_event_timeout starting')
            event_is_set = e.wait(t)
            logging.debug('event set: %s', event_is_set)
            if event_is_set:
                # check if the running queries are in system:active_requests
                logging.info('CHECKING SYSTEM:ACTIVE_REQUESTS FOR THE RUNNING QUERIES')
                result = self.run_cbq_query('select * from system:active_requests')
                if not result['metrics']['resultCount'] == 3:
                    self.threadFailure = True
                    logging.error(
                        'NOT ALL ACTIVE QUERIES ARE IN ACTIVE_REQUESTS, THERE SHOULD BE 3 QUERIES ACTIVE. %s'
                        ' QUERIES ARE ACTIVE.' % result['metrics']['resultCount'])
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # check if the queries' node fields accurately reflect the node they were started from
                logging.info("VERIFYING THAT ACTIVE_REQUESTS HAVE THE QUERIES MARKED WITH THE CORRECT NODES")
                node1 = self.run_cbq_query('select * from system:active_requests where node  =  "%s:%s"'
                                           % (self.servers[1].ip,self.servers[1].port))
                if not node1['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE QUERY ON THE REQUESTED NODE: "%s" IS NOT IN SYSTEM:ACTIVE_REQUESTS'
                                  % (self.servers[1].ip,self.servers[1].port))
                    print node1
                    return
                node2 = self.run_cbq_query('select * from system:active_requests where node  =  "%s:%s"'
                                           % (self.servers[2].ip,self.servers[2].port))
                if not node2['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE QUERY ON THE REQUESTED NODE: "%s:%s" IS NOT IN SYSTEM:ACTIVE_REQUESTS'
                                  % (self.servers[2].ip,self.servers[2].port))
                    print node2
                    return

                # check if a query can be accessed from system:active_requests using its requestId
                logging.info("CHECKING IF A QUERY CAN BE ACCESSED VIA ITS requestId")
                requestId = result['results'][1]['active_requests']['requestId']
                result = self.run_cbq_query('select * from system:active_requests where requestId  =  "%s"'
                                            % requestId)
                if not result['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE QUERY FOR requestId "%s" IS NOT IN ACTIVE_REQUESTS' % requestId)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # check if a query can be killed from system:active_requests using its requestId
                logging.info("CHECKING IF A QUERY CAN BE KILLED")
                self.run_cbq_query('delete from system:active_requests where requestId  =  "%s"' % requestId)
                result = self.run_cbq_query('select * from system:active_requests  where requestId  =  "%s"'
                                            % requestId)
                if not result['metrics']['resultCount'] == 0:
                    self.threadFailure = True
                    logging.error('THE QUERY FOR requestId "%s" WAS NOT KILLED AND IS STILL IN ACTIVE_REQUESTS'
                                  % requestId)
                    return

                # once the query is killed check if it is in system:completed_requests
                result = self.run_cbq_query('select * from system:completed_requests where requestId = "%s"'
                                            % requestId)
                if not result['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE QUERY FOR requestId "%s" WAS REMOVED FROM ACTIVE_REQUESTS BUT NOT PUT INTO '
                                  'COMPLETED_REQUESTS' % requestId)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                time.sleep(30)
                # check if the queries appear in system:completed_requests when they complete.
                logging.info('CHECKING IF ALL COMPLETED QUERIES ARE IN SYSTEM:COMPLETED_REQUESTS')
                result = self.run_cbq_query('select * from system:completed_requests')
                if not result['metrics']['resultCount'] == 2:
                    self.threadFailure = True
                    logging.error('THE QUERIES EITHER DID NOT COMPLETE RUNNING OR WERE NOT ADDED TO '
                                  'SYSTEM:COMPLETED_REQUESTS')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

    def run_purge_completed_requests(self, e, t):
        while not e.isSet():
            logging.debug('wait_for_event_timeout starting')
            event_is_set = e.wait(t)
            logging.debug('event set: %s', event_is_set)
            if event_is_set:
                time.sleep(60)
                logging.info('CHECKING IF SYSTEM:COMPLETED_REQUESTS HAS QUERIES IN IT')
                result = self.run_cbq_query('select * from system:completed_requests')
                if not result['metrics']['resultCount'] == 2:
                    self.threadFailure = True
                    logging.error('THERE ARE NO ITEMS INSIDE SYSTEM:COMPLETED_REQUESTS')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # check if the queries appear in system:completed_requests when they complete.
                logging.info('CHECKING IF SYSTEM:COMPLETED_REQUESTS CAN BE PURGED')
                self.run_cbq_query("delete from system:completed_requests")
                result = self.run_cbq_query('select * from system:completed_requests')
                if not result['metrics']['resultCount'] == 0:
                    self.threadFailure = True
                    logging.error('DELETE FAILED, THERE ARE STILL ITEMS INSIDE SYSTEM:COMPLETED_REQUESTS')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                query1 = threading.Thread(name='run_first_query', target=self.run_parallel_query,
                                          args=[self.servers[1]])
                query2 = threading.Thread(name='run_first_query', target=self.run_parallel_query,
                                          args=[self.servers[1]])
                query3 = threading.Thread(name='run_third_query', target=self.run_parallel_query,
                                          args=[self.servers[2]])
                query4 = threading.Thread(name='run_fourth_query', target=self.run_parallel_query,
                                          args=[self.servers[2]])
                query1.start()
                query2.start()
                query3.start()
                query4.start()

                query1.join(100)
                query2.join(100)
                query3.join(100)
                query4.join(100)

                # check if the queries can be purged selectively
                logging.info('CHECKING IF SYSTEM:COMPLETED_REQUESTS CAN BE PURGED BY NODE')
                self.run_cbq_query('delete from system:completed_requests where node = "%s:%s"'
                                   % (self.servers[2].ip,self.servers[2].port))
                result = self.run_cbq_query('select * from system:completed_requests')
                if not result['metrics']['resultCount'] == 2:
                    self.threadFailure = True
                    logging.error('DELETE FAILED, THERE ARE STILL ITEMS FROM NODE: "%s:%s"'
                                  'INSIDE SYSTEM:COMPLETED_REQUESTS'
                                  % (self.servers[2].ip,self.servers[2].port))
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # check if the queries can be purged by requestId
                logging.info('CHECKING IF SYSTEM:COMPLETED_REQUESTS CAN BE PURGED BY REQUESTID')
                requestId = result['results'][0]['completed_requests']['requestId']
                self.run_cbq_query('delete from system:completed_requests where requestId = "%s"' % requestId)
                result = self.run_cbq_query('select * from system:completed_requests')
                if not result['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('DELETE FAILED, THE QUERY FOR REQUESTID: "%s" IS STILL '
                                  'INSIDE SYSTEM:COMPLETED_REQUESTS' % requestId)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

    def run_filter_by_node(self, e, t):
        while not e.isSet():
            logging.debug('wait_for_event_timeout starting')
            event_is_set = e.wait(t)
            logging.debug('event set: %s', event_is_set)
            if event_is_set:
                logging.info('CHECKING IF SYSTEM:ACTIVE_REQUESTS RESULTS CAN BE FILTERED BY NODE')
                result = self.run_cbq_query('select * from system:active_requests')
                node1 = self.run_cbq_query('select * from system:active_requests where node = "%s:%s"'
                                           % (self.servers[2].ip,self.servers[2].port))
                if not node1['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE RESULTS OF THE QUERY ARE INCORRECT')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    print node1
                    return

                node2 = self.run_cbq_query('select * from system:active_requests where node = "%s:%s"'
                                           % (self.servers[1].ip,self.servers[1].port))
                if not node2['metrics']['resultCount'] == 3:
                    self.threadFailure = True
                    logging.error('THE RESULTS OF THE QUERY ARE INCORRECT')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    print node2
                    return

                time.sleep(30)

                logging.info('CHECKING IF SYSTEM:COMPLETED_REQUESTS RESULTS CAN BE FILTERED BY NODE')
                result = self.run_cbq_query('select * from system:completed_requests')
                node1 = self.run_cbq_query('select * from system:completed_requests where node = "%s:%s"'
                                           % (self.servers[2].ip,self.servers[1].port))
                if not node1['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE RESULTS OF THE QUERY ARE INACCURATE')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    print node1
                    return

                node2 = self.run_cbq_query('select * from system:completed_requests where node = "%s:%s"'
                                           % (self.servers[1].ip,self.servers[1].port))
                if not node2['metrics']['resultCount'] == 3:
                    self.threadFailure = True
                    logging.error('THE RESULTS OF THE QUERY ARE INACCURATE')
                    print (json.dumps(result, sort_keys=True, indent=3))
                    print node2
                    return

##############################################################################################
#
#   Monitoring Prepared Test Cases
#
##############################################################################################

    '''Runs the basic prepared monitoring checks:
            -Check that the number of uses for a prepared statement is correctly updated
            -Check that prepared statements appear in system:active_requests when ran
            -Check that prepared statements appear in system:completed_requests when ran.'''
    def test_prepared_monitoring(self):
        self.test_prepared_common_body()
        # Check that both prepareds are in system:prepareds
        self.query = "select * from system:prepareds"
        result = self.run_cbq_query()
        self.assertTrue(result['metrics']['resultCount'] == 2)

        name = result['results'][0]['prepareds']['name']
        uses = result['results'][0]['prepareds']['uses']
        self.assertTrue(uses == 1)

        secondname = result['results'][1]['prepareds']['name']

        e = threading.Event()
        thread1 = threading.Thread(name='run_simple_monitoring', target=self.run_simple_monitoring_prepared_check,
                                   args=(e, 2))
        thread2 = threading.Thread(name='run_prepared', target=self.execute_prepared, args=(name, self.servers[0]))
        thread3 = threading.Thread(name='run_prepared', target=self.execute_prepared,
                                   args=(secondname, self.servers[1]))
        thread1.start()
        thread2.start()
        thread3.start()

        e.set()

        thread1.join(100)
        thread2.join(100)
        thread3.join(100)
        self.assertFalse(self.threadFailure)

    '''Runs the basic prepared deletion checks:
            -Check if system:prepareds can be purged completely
            -Check if system:prepareds can be purged by node
            -Check if system:prepareds can be purged by preparedName.'''
    def test_prepared_deletion(self):
        self.test_prepared_common_body()
        self.query = "select * from system:prepareds"
        result = self.run_cbq_query()
        self.assertTrue(result['metrics']['resultCount'] == 2)

        #Check if you can delete everything in system:prepareds
        self.run_cbq_query("delete from system:prepareds")
        result = self.run_cbq_query("select * from system:prepareds")
        self.assertTrue(result['metrics']['resultCount'] == 0)

        #Reset prepared statements for next deletion check
        self.test_prepared_common_body()

        #Check if you can delete from system:prepareds by node
        self.query = "delete from system:prepareds where node = '%s:%s'" \
                     % (self.servers[0].ip,self.servers[0].port)
        self.run_cbq_query()
        result = self.run_cbq_query("select * from system:prepareds")
        self.assertTrue(result['metrics']['resultCount'] == 1)

        self.query = "delete from system:prepareds where node = '%s:%s'" \
                     % (self.servers[1].ip,self.servers[1].port)
        self.run_cbq_query()
        result = self.run_cbq_query("select * from system:prepareds")
        self.assertTrue(result['metrics']['resultCount'] == 0)

        #Reset prepared statements for next deletion check
        self.test_prepared_common_body()

        self.query = "select * from system:prepareds"
        result = self.run_cbq_query()
        prepared1 = result['results'][0]['prepareds']['name']
        prepared2 = result['results'][1]['prepareds']['name']

        #Check if system:prepareds can be deleted from by prepared name
        self.query = "delete from system:prepareds where name = '%s'" % prepared1
        self.run_cbq_query()
        result = self.run_cbq_query("select * from system:prepareds")
        self.assertTrue(result['metrics']['resultCount'] == 1)

        self.query = "delete from system:prepareds where name = '%s'" % prepared2
        self.run_cbq_query()
        result = self.run_cbq_query("select * from system:prepareds")
        self.assertTrue(result['metrics']['resultCount'] == 0)

    '''Runs the basic prepared filtering checks:
            -Check if system:prepareds can be filtered by prepared statement name
            -Check if system:prepareds can be purged by node.'''
    def test_prepared_filtering(self):
        self.test_prepared_common_body()
        # Check that both prepareds are in system:prepareds
        self.query = "select * from system:prepareds"

        result = self.run_cbq_query()
        self.assertTrue(result['metrics']['resultCount'] == 2)
        prepared1 = result['results'][0]['prepareds']['name']
        prepared2 = result['results'][1]['prepareds']['name']

        #Check if you can access prepared statements from system:prepareds by the prepared statement's name
        self.query = "select * from system:prepareds where name = '%s'" % prepared1
        name1 = self.run_cbq_query(server=self.servers[2])
        self.assertTrue(name1['metrics']['resultCount'] == 1)

        # Check if you can access prepared statements from system:prepareds by the prepared statement's name
        self.query = "select * from system:prepareds where name = '%s'" % prepared2
        name2 = self.run_cbq_query(server=self.servers[2])
        self.assertTrue(name2['metrics']['resultCount'] == 1)

        # Check to see if system:prepareds can be filtered by node
        self.query = "select * from system:prepareds where node = '%s:%s'" % \
                     (self.servers[0].ip,self.servers[0].port)
        node1 = self.run_cbq_query()
        self.assertTrue(node1['metrics']['resultCount'] == 1)

        # Check to see if system:prepareds can be filtered by node
        self.query = "select * from system:prepareds where node = '%s:%s'" \
                             % (self.servers[1].ip,self.servers[1].port)
        node2 = self.run_cbq_query()
        self.assertTrue(node2['metrics']['resultCount'] == 1)

        # Check to see if system:prepareds can be filtered by node
        self.query = "select * from system:prepareds where node = '%s:%s'" \
                     % (self.servers[2].ip,self.servers[2].port)
        node3 = self.run_cbq_query()
        self.assertTrue(node3['metrics']['resultCount'] == 0)

    '''Checks if a prepared request can be killed from system:active_requests:
            -Check if the request can be killed by its requestId
            -Check if the request can be killed by its name.
            -Check if requests can be killed by node.'''
    def test_prepared_kill_request(self):
        self.test_prepared_common_body("kill")
        # Check that both prepareds are in system:prepareds
        self.query = "select * from system:prepareds"
        result = self.run_cbq_query()
        self.assertTrue(result['metrics']['resultCount'] == 2)

        name = result['results'][0]['prepareds']['name']
        secondname = result['results'][1]['prepareds']['name']

        e = threading.Event()
        thread1 = threading.Thread(name='run_kill_request', target=self.run_kill_prepared_request,
                                   args=(e, 2))
        thread2 = threading.Thread(name='run_prepared', target=self.execute_prepared, args=(name, self.servers[0]))
        thread3 = threading.Thread(name='run_prepared', target=self.execute_prepared,
                                   args=(secondname, self.servers[1]))
        thread1.start()
        thread2.start()
        thread3.start()

        e.set()

        thread1.join(100)
        thread2.join(100)
        thread3.join(100)
        self.assertFalse(self.threadFailure)

##############################################################################################
#
#   Prepared Test Helper Functions
#
##############################################################################################

    def execute_prepared(self, prepared_name, server):
        result = self.run_cbq_query('EXECUTE "%s"' % prepared_name, server=server)

    '''Prepares two statements on different nodes.'''
    def test_prepared_common_body(self,test_type=None):
        self.query = "select * from system:prepareds"
        result = self.run_cbq_query()
        self.assertTrue(result['metrics']['resultCount'] == 0)

        for bucket in self.buckets:
            if test_type == "kill":
                self.query = "select * from default union select * from default union select * from default"
                self.prepared_common_body()

                self.query = "SELECT name, email FROM %s WHERE " % (bucket.name) + \
                             "(ANY skill IN %s.skills SATISFIES skill = 'skill2010' END)" % (
                                 bucket.name) + \
                             " AND (ANY vm IN %s.VMs SATISFIES vm.RAM between 1 and 5 END)" % (
                                 bucket.name) + \
                             "AND  NOT (job_title = 'Sales') ORDER BY name"
                self.prepared_common_body(server=self.servers[1])
            else:
                self.query = "SELECT name, email FROM %s WHERE "  % (bucket.name) +\
                             "(ANY skill IN %s.skills SATISFIES skill = 'skill2010' end)" % (
                                                                    bucket.name) +\
                             "AND (ANY vm IN %s.VMs SATISFIES vm.RAM = 5 end) " % (
                                                                bucket.name) +\
                             "AND  NOT (job_title = 'Sales') ORDER BY name"
                self.prepared_common_body()

                self.query = "SELECT name, email FROM %s WHERE "  % (bucket.name) +\
                             "(ANY skill IN %s.skills SATISFIES skill = 'skill2010' END)" % (
                                                                          bucket.name) +\
                            " AND (ANY vm IN %s.VMs SATISFIES vm.RAM between 1 and 5 END)"  % (
                                                                    bucket.name) +\
                            "AND  NOT (job_title = 'Sales') ORDER BY name"
                self.prepared_common_body(server=self.servers[1])

    def run_simple_monitoring_prepared_check(self, e, t):
        while not e.isSet():
            logging.debug('wait_for_event_timeout starting')
            event_is_set = e.wait(t)
            logging.debug('event set: %s', event_is_set)
            if event_is_set:
                logging.info('CHECKING # OF USES FOR THE PREPARED STATEMENT ON NODE "%s"' % self.servers[0])
                result = self.run_cbq_query('select * from system:prepareds')
                if not result['results'][0]['prepareds']['uses'] == 2:
                    self.threadFailure = True
                    logging.error(
                        'THE PREPARED STATEMENT SHOULD HAVE 2 USES, BUT ONLY "%s" USES HAVE BEEN REPORTED'
                        % result['results'][0]['prepareds']['uses'])
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # check if the running queries are in system:active_requests
                logging.info('CHECKING SYSTEM:ACTIVE_REQUESTS FOR THE RUNNING QUERIES')
                result = self.run_cbq_query('select * from system:active_requests')
                if not result['metrics']['resultCount'] == 3:
                    self.threadFailure = True
                    logging.error(
                        'NOT ALL ACTIVE QUERIES ARE IN ACTIVE_REQUESTS, THERE SHOULD BE 2 QUERIES ACTIVE. %s'
                        ' QUERIES ARE ACTIVE.' % result['metrics']['resultCount'])
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                time.sleep(30)
                # Check if the completed query is in system:completed_requests
                logging.info('CHECKING SYSTEM:COMPLETED_REQUESTS FOR THE COMPLETED QUERIES')
                result = self.run_cbq_query('select * from system:completed_requests')
                if not result['metrics']['resultCount'] == 6:
                    self.threadFailure = True
                    logging.error(
                        'COMPLETED REQUESTS IS DIFFERENT THAN WHAT IS EXPECTED, THERE SHOULD BE 4 QUERIES COMPLETED. %s'
                        ' QUERIES ARE COMPLETED.' % result['metrics']['resultCount'])
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

    def run_kill_prepared_request(self, e, t):
        while not e.isSet():
            logging.debug('wait_for_event_timeout starting')
            event_is_set = e.wait(t)
            logging.debug('event set: %s', event_is_set)
            if event_is_set:
                result = self.run_cbq_query('select * from system:active_requests')
                # check if a query can be accessed from system:active_requests using its requestId
                logging.info("CHECKING IF A QUERY CAN BE ACCESSED VIA ITS requestId")
                requestId = result['results'][0]['active_requests']['requestId']
                result = self.run_cbq_query('select * from system:active_requests where requestId  =  "%s"'
                                            % requestId)
                if not result['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE QUERY FOR requestId "%s" IS NOT IN ACTIVE_REQUESTS' % requestId)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # check if a query can be killed from system:active_requests using its requestId
                logging.info("CHECKING IF A QUERY CAN BE KILLED BY REQUESTID")
                self.run_cbq_query('delete from system:active_requests where requestId  =  "%s"' % requestId)
                result = self.run_cbq_query('select * from system:active_requests  where requestId  =  "%s"'
                                            % requestId)
                if not result['metrics']['resultCount'] == 0:
                    self.threadFailure = True
                    logging.error('THE QUERY FOR requestId "%s" WAS NOT KILLED AND IS STILL IN ACTIVE_REQUESTS'
                                  % requestId)
                    return

                result = self.run_cbq_query('select * from system:active_requests')
                if not result['metrics']['resultCount'] == 2:
                    self.threadFailure = True
                    logging.error(
                        'NOT ALL ACTIVE QUERIES ARE IN ACTIVE_REQUESTS, THERE SHOULD BE 2 QUERIES ACTIVE. %s'
                        ' QUERIES ARE ACTIVE.' % result['metrics']['resultCount'])
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                # Check if a request can be killed by query name
                logging.info("CHECKING IF A QUERY CAN BE KILLED BY NAME")
                preparedName = result['results'][1]['active_requests']['preparedName']
                self.run_cbq_query('delete from system:active_requests where preparedName  =  "%s"' % preparedName)
                result = self.run_cbq_query('select * from system:active_requests  where preparedName  =  "%s"'
                                            % preparedName)
                if not result['metrics']['resultCount'] == 0:
                    self.threadFailure = True
                    logging.error('THE QUERY FOR name "%s" WAS NOT KILLED AND IS STILL IN ACTIVE_REQUESTS'
                                  % preparedName)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                self.query = "select * from system:prepareds"
                result = self.run_cbq_query()
                name = result['results'][0]['prepareds']['name']
                secondname = result['results'][1]['prepareds']['name']

                thread1 = threading.Thread(name='run_prepared', target=self.execute_prepared,
                                           args=(name, self.servers[0]))
                thread2 = threading.Thread(name='run_prepared', target=self.execute_prepared,
                                           args=(name, self.servers[0]))
                thread3 = threading.Thread(name='run_prepared', target=self.execute_prepared,
                                           args=(secondname, self.servers[1]))

                thread1.start()
                thread2.start()
                thread3.start()

                # Check if a request can be killed by query node
                logging.info("CHECKING IF A QUERY CAN BE KILLED BY NODE")
                self.run_cbq_query('delete from system:active_requests where node  =  "%s:%s"'
                                   % (self.servers[0].ip,self.servers[0].port))
                result = self.run_cbq_query('select * from system:active_requests  where node  =  "%s:%s"'
                                            % (self.servers[0].ip,self.servers[0].port))
                if not result['metrics']['resultCount'] == 1:
                    self.threadFailure = True
                    logging.error('THE QUERIES FOR node "%s" WERE NOT KILLED AND ARE STILL IN ACTIVE_REQUESTS'
                                  % self.servers[0].ip)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

                result = self.run_cbq_query("select * from system:active_requests")
                if not result['metrics']['resultCount'] == 2:
                    self.threadFailure = True
                    logging.error('THE QUERIES FOR node "%s" WERE NOT KILLED AND ARE STILL IN ACTIVE_REQUESTS'
                                  % self.servers[0].ip)
                    print (json.dumps(result, sort_keys=True, indent=3))
                    return

##############################################################################################
#
#   Configuration Test Settings (Completed_requests configuration settings)
#
##############################################################################################

    '''Check that the configuration settings for system:completed_requests can be changed'''
    def test_set_completed_config(self):
        # Change the collection setting
        response,content = self.rest.set_completed_requests_collection_duration(self.master, 10000)
        result = json.loads(content)
        self.assertTrue(result['completed-threshold'] == 10000)

        response,content = self.rest.set_completed_requests_collection_duration(self.master, 1000)
        result = json.loads(content)
        self.assertTrue(result['completed-threshold'] == 1000)

        # Change the retention setting
        response,content = self.rest.set_completed_requests_max_entries(self.master, 10)
        result = json.loads(content)
        self.assertTrue(result['completed-limit'] == 10)

        response,content = self.rest.set_completed_requests_max_entries(self.master, 4000)
        result = json.loads(content)
        self.assertTrue(result['completed-limit'] == 4000)

    '''Check that you can change the maximum number of entries that system:completed requests keeps at one time.'''
    def test_retention_config(self):
        self.rest.set_completed_requests_max_entries(self.master, 4000)
        num_entries = 10
        # Change the retention setting to only hold the amount of queries specified by num_entries
        response,content = self.rest.set_completed_requests_max_entries(self.master, num_entries)
        result = json.loads(content)
        self.assertTrue(result['completed-limit'] == num_entries)

        # Run more than num_entries(10) queries
        for i in range(num_entries*2):
            self.run_cbq_query('select * from default union select * from default')

        result = self.run_cbq_query('select * from system:completed_requests')
        print (json.dumps(result, sort_keys=True, indent=3))
        self.assertTrue(result['metrics']['resultCount'] == 10)

        # negative should disable the limit
        num_entries = -1
        response, content = self.rest.set_completed_requests_max_entries(self.master, num_entries)
        result = json.loads(content)
        self.assertTrue(result['completed-limit'] == num_entries)

        for i in range(100):
            self.run_cbq_query('select * from default')

        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 110)

        # 0 should disable logging
        num_entries = 0
        response, content = self.rest.set_completed_requests_max_entries(self.master, num_entries)
        result = json.loads(content)
        self.assertTrue(result['completed-limit'] == num_entries)

        self.run_cbq_query('select * from default')

        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 110)
        self.rest.set_completed_requests_max_entries(self.master, 4000)

    '''Check that you can change the min duration a query has to run for to be stored in system:completed_requests'''
    def test_collection_config(self):
        self.rest.set_completed_requests_collection_duration(self.master, 1000)
        # Test the default setting of 1 second
        self.run_cbq_query('select * from system:active_requests')
        self.run_cbq_query('select * from default')
        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 1)
        # Wipe the completed logs for the next test
        self.run_cbq_query('delete from system:completed_requests')

        # Change the minimum number of milliseconds a query needs to run for to be collected, in this case 8 seconds
        min_duration = 5000
        # Change the collection setting
        response,content = self.rest.set_completed_requests_collection_duration(self.master, min_duration)
        result = json.loads(content)
        self.assertTrue(result['completed-threshold'] == min_duration)
        # Construct nonsense queries that run for 5 seconds
        self.run_cbq_query('select * from default union select * from default union select * from default')
        self.run_cbq_query('select * from default union select * from default union select * from default')
        # Run a query that runs for a normal amount of time ~2 seconds
        self.run_cbq_query('select * from default')
        self.run_cbq_query('select * from default')

        # Only the queries run for longer than 8 seconds should show up
        result=self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 2)
        # Wipe the completed logs for the next test
        self.run_cbq_query('delete from system:completed_requests')

        # Check 1 millisecond, basically any query should show up here
        min_duration = 1
        response,content = self.rest.set_completed_requests_collection_duration(self.master, min_duration)
        result = json.loads(content)
        self.assertTrue(result['completed-threshold'] == min_duration)

        self.run_cbq_query('select * from system:active_requests')
        self.run_cbq_query('select * from default')
        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 2)

        # Disable logging
        min_duration = -1
        # Change the collection setting
        response, content = self.rest.set_completed_requests_collection_duration(self.master, min_duration)
        result = json.loads(content)
        self.assertTrue(result['completed-threshold'] == min_duration)
        self.run_cbq_query('delete from system:completed_requests')
        self.run_cbq_query('select * from default')
        self.run_cbq_query('select * from default')

        # No queries should appear
        result = self.run_cbq_query('select * from system:completed_requests')
        self.assertTrue(result['metrics']['resultCount'] == 0)

        self.rest.set_completed_requests_collection_duration(self.master, 1000)

##############################################################################################
#
#   Common Helper Functions
#
##############################################################################################

    def run_cbq_query(self, query=None, min_output_size=10, server=None, query_params={}, is_prepared=False,
                      encoded_plan=None):
        self.log.info("-"*100)
        if query is None:
            query = self.query
        if server is None:
           server = self.master
           if self.input.tuq_client and "client" in self.input.tuq_client:
               server = self.tuq_client
        cred_params = {'creds': []}
        rest = RestConnection(server)
        username = rest.username
        password = rest.password
        cred_params['creds'].append({'user': username, 'pass': password})
        for bucket in self.buckets:
            if bucket.saslPassword:
                cred_params['creds'].append({'user': 'local:%s' % bucket.name, 'pass': bucket.saslPassword})
        query_params.update(cred_params)
        if self.use_rest:
            query_params.update({'scan_consistency': self.scan_consistency})
            self.log.info('RUN QUERY %s' % query)
            if hasattr(self, 'query_params') and self.query_params:
                query_params = self.query_params

            if self.analytics:
                query = query + ";"
                for bucket in self.buckets:
                    query = query.replace(bucket.name,bucket.name+"_shadow")
                self.log.info('RUN QUERY %s' % query)
                result = rest.analytics_tool(query, 8095, query_params=query_params,
                                                               is_prepared=is_prepared, named_prepare=self.named_prepare,
                                                               encoded_plan=encoded_plan, servers=self.servers)

            else :
                result = rest.query_tool(query, self.n1ql_port, query_params=query_params,
                                                           is_prepared=is_prepared, named_prepare=self.named_prepare,
                                                           encoded_plan=encoded_plan, servers=self.servers)
        else:
            if self.version == "git_repo":
                output = self.shell.execute_commands_inside("$GOPATH/src/github.com/couchbase/query/" +\
                                                            "shell/cbq/cbq ","","","","","","")
            else:
                if not(self.isprepared):
                    query = query.replace('"', '\\"')
                    query = query.replace('`', '\\`')
                    cmd = "%s/cbq  -engine=http://%s:%s/ -q -u %s -p %s" % (
                    self.path, server.ip, server.port, username, password)

                    output = self.shell.execute_commands_inside(cmd,query,"","","","","")
                    if not(output[0] == '{'):
                        output1 = '{'+str(output)
                    else:
                        output1 = output
                    result = json.loads(output1)
        if isinstance(result, str) or 'errors' in result:
            raise CBQError(result, server.ip)
        if 'metrics' in result:
            self.log.info("TOTAL ELAPSED TIME: %s" % result["metrics"]["elapsedTime"])
        return result

    def prepared_common_body(self,server=None):
        self.isprepared = True
        result_no_prepare = self.run_cbq_query(server=server)['results']
        if self.named_prepare:
            if 'concurrent' not in self.named_prepare:
                self.named_prepare=self.named_prepare + "_" +str(uuid.uuid4())[:4]
            query = "PREPARE %s from %s" % (self.named_prepare,self.query)
        else:
            query = "PREPARE %s" % self.query
        prepared = self.run_cbq_query(query=query,server=server)['results'][0]
        if self.encoded_prepare and len(self.servers) > 1:
            encoded_plan=prepared['encoded_plan']
            result_with_prepare = self.run_cbq_query(query=prepared, is_prepared=True, encoded_plan=encoded_plan,
                                                     server=server)['results']
        else:
            result_with_prepare = self.run_cbq_query(query=prepared, is_prepared=True,server=server)['results']
        if(self.cover):
            self.assertTrue("IndexScan in %s" % result_with_prepare)
            self.assertTrue("covers in %s" % result_with_prepare)
            self.assertTrue("filter_covers in %s" % result_with_prepare)
            self.assertFalse('ERROR' in (str(word).upper() for word in result_with_prepare))
        msg = "Query result with prepare and without doesn't match.\nNo prepare: %s ... %s\nWith prepare: %s ... %s"
        self.assertTrue(sorted(result_no_prepare) == sorted(result_with_prepare),
                          msg % (result_no_prepare[:100],result_no_prepare[-100:],
                                 result_with_prepare[:100],result_with_prepare[-100:]))