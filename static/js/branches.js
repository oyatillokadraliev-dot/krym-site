(function () {
  'use strict';

  var tabs = document.querySelectorAll('.branch-switch-btn');
  var panels = document.querySelectorAll('.branch-panel');

  function activate(target, updateHash) {
    tabs.forEach(function (tab) {
      var isTarget = tab.dataset.target === target;
      tab.classList.toggle('is-active', isTarget);
      tab.setAttribute('aria-selected', isTarget ? 'true' : 'false');
    });

    panels.forEach(function (panel) {
      var isTarget = panel.id.toLowerCase().includes(target);
      panel.classList.toggle('is-active', isTarget);
    });

    if (updateHash) {
      history.replaceState(null, '', '#' + target);
    }
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      activate(tab.dataset.target, true);
    });
  });

  var initial = window.location.hash.replace('#', '');
  if (initial === 'nachalovskoe' || initial === 'babajka') {
    activate(initial, false);
  }
})();
