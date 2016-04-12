'use strict';

function analysisMonitorFactory (
  $http, $log, $window, analysisService, analysisDetailService, settings
) {
  var analysesList = [];
  var analysesRunningList = [];
  var analysesGlobalList = [];
  var analysesRunningGlobalList = [];
  var analysesDetail = {};

  var initializeAnalysesDetail = function (uuid) {
    analysesDetail[uuid] = {
      refineryImport: {
        status: '',
        percent_done: 0
      },
      galaxyImport: {
        status: '',
        percent_done: 0
      },
      galaxyAnalysis: {
        status: '',
        percent_done: 0
      },
      galaxyExport: {
        status: '',
        percent_done: 0
      },
      cancelingAnalyses: false
    };
  };

  var humanizeTimeObj = function (param) {
    var a = param.split(/[^0-9]/);
    var testDate = Date.UTC(a[0], a[1] - 1, a[2], a[3], a[4], a[5]);
    var curDate = new Date().getTimezoneOffset() * 60 * 1000;
    var offsetDate = testDate + curDate;
    var unixtime = offsetDate / 1000;

    return settings.humanize.relativeTime(unixtime);
  };

  var isObjExist = function (data) {
    if (typeof data !== 'undefined' && data !== null) {
      return true;
    }
    return false;
  };

  var formatMilliTime = function (timeStr) {
    var milliTime = Date.parse(timeStr);
    return milliTime;
  };

  var isParamValid = function (data) {
    if (typeof data !== 'undefined' && typeof data.time_start !== 'undefined' &&
      typeof data.time_end !== 'undefined' && data.time_start !== null &&
      data.time_end !== null) {
      return 'complete';
    } else if (typeof data !== 'undefined' && typeof data.time_start !== 'undefined' &&
      data.time_start !== null) {
      return 'running';
    }
    return 'false';
  };

  var averagePercentDone = function (numArr) {
    var totalSum = 0;
    for (var i = 0; i < numArr.length; i++) {
      totalSum = totalSum + numArr[i];
    }
    if (totalSum > 0) {
      return totalSum / numArr.length;
    }
    return totalSum;
  };

  var createElapseTime = function (timeObj) {
    var startTime;
    var endTime;
    var elapseMilliTime;

    if (isParamValid(timeObj) === 'complete') {
      startTime = formatMilliTime(timeObj.time_start);
      endTime = formatMilliTime(timeObj.time_end);
      elapseMilliTime = endTime - startTime;
      return elapseMilliTime;
    } else if (isParamValid(timeObj) === 'running') {
      var curTime = new Date() - new Date().getTimezoneOffset() * 60 * 1000;
      startTime = formatMilliTime(timeObj.time_start);
      elapseMilliTime = curTime - startTime;
      return elapseMilliTime;
    }
    return null;
  };

  // process responses from api
  var addElapseAndHumanTime = function (data) {
    angular.copy(data, analysesList);
    for (var j = 0; j < analysesList.length; j++) {
      analysesList[j].elapseTime = createElapseTime(analysesList[j]);
      if (isObjExist(analysesList[j].time_start)) {
        analysesList[j].humanizeStartTime = humanizeTimeObj(analysesList[j].time_start);
      }
      if (isObjExist(analysesList[j].time_end)) {
        analysesList[j].humanizeEndTime = humanizeTimeObj(analysesList[j].time_end);
      }
    }
  };

  // Copies and sorts analyses list
  var processAnalysesList = function (data, params) {
    if ('status__in' in params && 'data_set__uuid' in params) {
      angular.copy(data, analysesRunningList);
    } else if ('status__in' in params) {
      angular.copy(data, analysesRunningGlobalList);
    } else if ('limit' in params && 'data_set__uuid' in params) {
      addElapseAndHumanTime(data);
    } else {
      angular.copy(data, analysesGlobalList);
    }
  };

  // Ajax calls, grabs the entire analysis list for a particular data set
  var getAnalysesList = function (_params_) {
    var params = _params_ || {};

    var analysis = analysisService.query(params);
    analysis.$promise.then(function (response) {
      processAnalysesList(response.objects, params);
    });

    return analysis.$promise;
  };

  var postCancelAnalysis = function (uuid) {
    return $http({
      method: 'POST',
      url: '/analysis_manager/analysis_cancel/',
      data: {
        csrfmiddlewaretoken: $window.csrf_token,
        uuid: uuid
      },
      headers: {
        'X-Requested-With': 'XMLHttpRequest'
      }
    });
  };

  var setAnalysesStatus = function (data, uuid) {
    angular.forEach(data, function (dataArr, stage) {
      var tempArr = [];
      var failureFlag = false;
      if (typeof stage !== 'undefined' && dataArr.length > 1) {
        for (var i = 0; i < dataArr.length; i++) {
          tempArr.push(dataArr[i].percent_done);
          if (dataArr[i].state === 'FAILURE') {
            failureFlag = true;
          }
        }
        var avgPercentDone = averagePercentDone(tempArr);
        if (failureFlag) {
          analysesDetail[uuid][stage] = {
            state: 'FAILURE',
            percent_done: null
          };
        } else if (avgPercentDone === 100) {
          analysesDetail[uuid][stage] = {
            state: 'SUCCESS',
            percent_done: avgPercentDone
          };
        } else {
          analysesDetail[uuid][stage] = {
            state: 'PROGRESS',
            percent_done: avgPercentDone
          };
        }
      } else if (dataArr.length === 1) {
        analysesDetail[uuid][stage] = dataArr[0];
      }
    });
  };

  var processAnalysesGlobalDetail = function (data, uuid) {
    if (!(analysesDetail.hasOwnProperty(uuid))) {
      initializeAnalysesDetail(uuid);
    }
    setAnalysesStatus(data, uuid);
  };

  var getAnalysesDetail = function (uuid) {
    return analysisDetailService.query({
      uuid: uuid
    }).$promise.then(function (response) {
      processAnalysesGlobalDetail(response, uuid);
    }, function () {
      $log.error('Error accessing analysis monitoring API');
    });
  };

  return {
    getAnalysesList: getAnalysesList,
    getAnalysesDetail: getAnalysesDetail,
    postCancelAnalysis: postCancelAnalysis,
    analysesList: analysesList,
    analysesGlobalList: analysesGlobalList,
    analysesDetail: analysesDetail,
    analysesRunningList: analysesRunningList,
    analysesRunningGlobalList: analysesRunningGlobalList
  };
}

angular
  .module('refineryAnalysisMonitor')
  .factory('analysisMonitorFactory', [
    '$http',
    '$log',
    '$window',
    'analysisService',
    'analysisDetailService',
    'settings',
    analysisMonitorFactory
  ]);
