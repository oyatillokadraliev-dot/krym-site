(function () {
  'use strict';

  var tabs = document.querySelectorAll('[data-price-target]');
  var panels = document.querySelectorAll('.price-panel');

  function activate(target) {
    tabs.forEach(function (tab) {
      var isTarget = tab.dataset.priceTarget === target;
      tab.classList.toggle('is-active', isTarget);
      tab.setAttribute('aria-selected', isTarget ? 'true' : 'false');
    });

    panels.forEach(function (panel) {
      var isTarget = panel.id.toLowerCase().includes(target);
      panel.classList.toggle('is-active', isTarget);
    });
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      activate(tab.dataset.priceTarget);
    });
  });
})();
