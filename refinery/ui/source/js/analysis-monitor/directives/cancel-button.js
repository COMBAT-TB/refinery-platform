'use strict';


function rpAnalysisMonitorCancelConfirm (settings) {
  return {
    controller: 'AnalysisMonitorCtrl',
    link: function (scope, element, attr) {
      var msg = '<h3>Cancel Analysis?</h3><p>Are you sure you want to' +
        ' cancel this analysis?</p>';
      var clickAction = attr.confirmedClick;
      element.bind('click', function () {
        settings.bootbox.confirm(msg, function (result) {
          if (result) {
            scope.$eval(clickAction);
          }
        });
      });
    }
  };
}

angular
  .module('refineryAnalysisMonitor')
  .directive('rpAnalysisMonitorCancelConfirm', [
    'settings',
    rpAnalysisMonitorCancelConfirm
  ]);
