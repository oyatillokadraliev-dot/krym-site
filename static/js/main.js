(function () {
  'use strict';

  var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Прелоадер: бегущий силуэт + процент ---------- */
  (function preloader() {
    var el = document.getElementById('preloader');
    var percentEl = document.getElementById('preloaderPercent');
    var barFill = document.getElementById('preloaderBarFill');
    if (!el) return;

    document.documentElement.classList.add('has-preloader');

    var progress = 0;
    var target = 0;
    var done = false;
    var finished = false;

    function bump() {
      // имитация неравномерной загрузки, плюс подталкиваем к 100% на load.
      // Потолок 96%, а не 100 — чтобы полоса не "зависала" ровно на 100
      // раньше вызова finish() и не создавала впечатление робота.
      var step = Math.random() * 9 + 2;
      target = Math.min(target + step, done ? 100 : 96);
    }

    var bumpTimer = setInterval(bump, 180);
    bump();

    function tick() {
      if (finished) return;
      progress += (target - progress) * 0.18;
      if (target - progress < 0.15) progress = target;
      var shown = Math.min(Math.round(progress), 100);
      if (percentEl) percentEl.textContent = (shown < 10 ? '0' : '') + shown;
      if (barFill) barFill.style.width = shown + '%';

      if (shown >= 100) {
        finish();
        return;
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    function finish() {
      if (finished) return;
      finished = true;
      clearInterval(bumpTimer);
      clearTimeout(safetyTimer);
      if (percentEl) percentEl.textContent = '100';
      if (barFill) barFill.style.width = '100%';
      document.body.classList.add('is-loaded');
      document.documentElement.classList.remove('has-preloader');
      setTimeout(function () {
        if (el && el.parentNode) el.parentNode.removeChild(el);
      }, 650);
    }

    window.addEventListener('load', function () {
      done = true;
      target = 100;
    });

    // Страховка: даже если window.load не сработает (медленная мобильная
    // сеть, сторонний скрипт завис) — прелоадер всё равно закрывается
    // максимум через 3.5с, чтобы не держать сайт в бесконечной загрузке.
    var safetyTimer = setTimeout(function () {
      done = true;
      target = 100;
      finish();
    }, 3500);
  })();

  /* ---------- Хэдер при скролле ---------- */
  var header = document.getElementById('siteHeader');
  function onScroll() {
    if (window.scrollY > 24) {
      header.classList.add('is-scrolled');
    } else {
      header.classList.remove('is-scrolled');
    }
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------- Мобильное меню ---------- */
  var navToggle = document.getElementById('navToggle');
  var mainNav = document.getElementById('mainNav');
  var navScrim = document.getElementById('navScrim');

  function closeNav() {
    mainNav.classList.remove('is-open');
    navScrim.classList.remove('is-visible');
    navToggle.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
  }

  function openNav() {
    mainNav.classList.add('is-open');
    navScrim.classList.add('is-visible');
    navToggle.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
  }

  navToggle.addEventListener('click', function () {
    var isOpen = mainNav.classList.contains('is-open');
    if (isOpen) {
      closeNav();
    } else {
      openNav();
    }
  });

  navScrim.addEventListener('click', closeNav);

  mainNav.querySelectorAll('a').forEach(function (link) {
    link.addEventListener('click', closeNav);
  });

  window.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      closeNav();
    }
  });

  /* ---------- Scroll-reveal для секций (в духе Sportlife) ---------- */
  (function scrollReveal() {
    var targets = document.querySelectorAll(
      '.section-head, .direction-card, .branch-card, .trainer-card, ' +
      '.schedule-table, .review-card, .price-group, .club-card'
    );
    if (!targets.length) return;

    if (prefersReduced || typeof IntersectionObserver === 'undefined') {
      targets.forEach(function (t) { t.classList.add('is-visible'); });
      return;
    }

    targets.forEach(function (t) { t.classList.add('scroll-reveal'); });

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry, i) {
        if (entry.isIntersecting) {
          var el = entry.target;
          setTimeout(function () {
            el.classList.add('is-visible');
          }, (i % 4) * 70);
          io.unobserve(el);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });

    targets.forEach(function (t) { io.observe(t); });
  })();

  /* ---------- Параллакс для hero-свечения и крупных номеров секций ---------- */
  (function parallax() {
    if (prefersReduced) return;
    var glow = document.querySelector('.hero-glow');
    var numbers = document.querySelectorAll('.section-number');
    if (!glow && !numbers.length) return;

    var ticking = false;
    function onScrollParallax() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        var y = window.scrollY;
        if (glow) {
          glow.style.transform = 'translate3d(0,' + (y * 0.18) + 'px,0)';
        }
        numbers.forEach(function (num) {
          var rect = num.getBoundingClientRect();
          var offset = (window.innerHeight - rect.top) * 0.04;
          num.style.transform = 'translate3d(0,' + Math.max(-30, Math.min(30, offset)) + 'px,0)';
        });
        ticking = false;
      });
    }
    window.addEventListener('scroll', onScrollParallax, { passive: true });
    onScrollParallax();
  })();

  /* ---------- Фотогалерея с навигацией (блок "Сауна" и т.п.) ---------- */
  (function photoGallery() {
    document.querySelectorAll('[data-gallery]').forEach(function (root) {
      var slides = root.querySelectorAll('[data-gallery-slide]');
      var prev = root.querySelector('[data-gallery-prev]');
      var next = root.querySelector('[data-gallery-next]');
      var counter = root.querySelector('[data-gallery-counter]');
      if (!slides.length) return;

      var index = 0;

      function render() {
        slides.forEach(function (s, i) {
          s.classList.toggle('is-active', i === index);
        });
        if (counter) counter.textContent = (index + 1) + ' / ' + slides.length;
      }

      if (prev) prev.addEventListener('click', function () {
        index = (index - 1 + slides.length) % slides.length;
        render();
      });
      if (next) next.addEventListener('click', function () {
        index = (index + 1) % slides.length;
        render();
      });

      render();
    });
  })();

  /* ---------- Простые слайдеры (направления / тренеры / отдых) ---------- */
  (function sliders() {
    var sliders = document.querySelectorAll('[data-slider]');
    sliders.forEach(function (root) {
      var track = root.querySelector('[data-slider-track]');
      var prev = root.querySelector('[data-slider-prev]');
      var next = root.querySelector('[data-slider-next]');
      if (!track) return;

      function step() {
        var card = track.querySelector(':scope > *');
        if (!card) return 320;
        var style = window.getComputedStyle(track);
        var gap = parseFloat(style.columnGap || style.gap || 24);
        return card.getBoundingClientRect().width + gap;
      }

      if (prev) prev.addEventListener('click', function () {
        track.scrollBy({ left: -step(), behavior: 'smooth' });
      });
      if (next) next.addEventListener('click', function () {
        track.scrollBy({ left: step(), behavior: 'smooth' });
      });
    });
  })();

  /* ---------- Единственный оркестрованный момент: hero on load ---------- */
  function runHeroReveal() {
    if (prefersReduced || typeof anime === 'undefined') {
      document.querySelectorAll('.hero-reveal').forEach(function (el) {
        el.style.opacity = '1';
      });
      return;
    }

    var words = document.querySelectorAll('[data-reveal="word"]');
    var eyebrow = document.querySelector('[data-reveal="eyebrow"]');
    var fades = document.querySelectorAll('[data-reveal="fade"]');

    anime.set(words, { opacity: 0, translateY: 28 });
    anime.set(eyebrow, { opacity: 0, translateY: 10 });
    anime.set(fades, { opacity: 0, translateY: 14 });

    var tl = anime.timeline({
      easing: 'cubicBezier(0.16, 1, 0.3, 1)'
    });

    tl.add({
      targets: eyebrow,
      opacity: [0, 1],
      translateY: [10, 0],
      duration: 500
    })
      .add({
        targets: words,
        opacity: [0, 1],
        translateY: [28, 0],
        duration: 700,
        delay: anime.stagger(90)
      }, '-=250')
      .add({
        targets: fades,
        opacity: [0, 1],
        translateY: [14, 0],
        duration: 600,
        delay: anime.stagger(110)
      }, '-=350');
  }

  // Запускаем раскрытие hero только после того, как прелоадер исчез
  // (если прелоадера на странице нет — сработает сразу).
  if (document.getElementById('preloader')) {
    document.body.addEventListener('transitionend', function handler(e) {
      if (e.target.id === 'preloader' && e.propertyName === 'opacity') {
        document.body.removeEventListener('transitionend', handler);
      }
    });
    var waited = 0;
    var waitTimer = setInterval(function () {
      waited += 50;
      if (document.body.classList.contains('is-loaded') || waited > 5000) {
        clearInterval(waitTimer);
        runHeroReveal();
      }
    }, 50);
  } else {
    runHeroReveal();
  }
})();
