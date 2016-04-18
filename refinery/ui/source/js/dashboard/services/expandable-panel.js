'use strict';

angular
  .module('refineryDashboard')
  .factory('dashboardExpandablePanelService', ['_',
    function (_) {
      var listeners = {
        expander: [],
        collapser: []
      };

      /**
       * Trigger a stack of listeners
       * @type {function}
       * @param  {string} stack Listener's name.
       */
      function trigger (stack) {
        if (_.isArray(listeners[stack])) {
          for (var i = 0, len = listeners[stack].length; i < len; i++) {
            if (_.isFunction(listeners[stack][i])) {
              listeners[stack][i]();
            }
          }
        }
      }

      return {
        trigger: trigger,
        expander: listeners.expander,
        collapser: listeners.collapser
      };
    }
  ]);
