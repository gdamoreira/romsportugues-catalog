/* ============================================================
   ROMs em Português — catalogue interactions
   No dependencies. Works when opened directly from disk.
   ============================================================ */

(function () {
  "use strict";

  var grid = document.getElementById("grid");
  if (!grid) return;

  var search = document.getElementById("search");
  var searchClear = document.getElementById("search-clear");
  var consoleSelect = document.getElementById("console");
  var genreSelect = document.getElementById("genre");
  var sortSelect = document.getElementById("sort");
  var results = document.getElementById("results");
  var empty = document.getElementById("empty");
  var resetButton = document.getElementById("reset");
  var emptyReset = document.getElementById("empty-reset");
  var toTop = document.getElementById("to-top");
  var themeToggle = document.getElementById("theme-toggle");
  var number = new Intl.NumberFormat("pt-BR");

  function norm(value) {
    return (value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  var items = Array.prototype.map.call(
    grid.querySelectorAll(".rom-card"),
    function (element, index) {
      return {
        element: element,
        index: index,
        name: norm(element.dataset.name),
        console: element.dataset.console || "",
        genres: (element.dataset.genres || "")
          .split("|")
          .filter(Boolean)
          .map(norm),
        year: parseInt(element.dataset.year, 10) || 0,
        search: norm(element.dataset.search),
      };
    }
  );

  var lastSort = "";
  var searchTimer = null;

  function applyFilter() {
    var query = norm(search.value);
    var consoleValue = consoleSelect.value;
    var genreValue = norm(genreSelect.value);
    var shown = 0;

    for (var i = 0; i < items.length; i += 1) {
      var item = items[i];
      var matches =
        (!query || item.search.indexOf(query) !== -1) &&
        (!consoleValue || item.console === consoleValue) &&
        (!genreValue || item.genres.indexOf(genreValue) !== -1);

      item.element.hidden = !matches;
      if (matches) shown += 1;
    }

    results.textContent =
      number.format(shown) + " de " + number.format(items.length) + " ROMs";
    empty.classList.toggle("visible", shown === 0);
    searchClear.hidden = search.value.length === 0;
  }

  function byName(a, b) {
    return a.name.localeCompare(b.name, "pt-BR");
  }

  function applySort(force) {
    var sort = sortSelect.value;
    if (!force && sort === lastSort) return;
    lastSort = sort;

    var sorted = items.slice();
    if (sort === "name") {
      sorted.sort(byName);
    } else if (sort === "year-desc") {
      sorted.sort(function (a, b) {
        return b.year - a.year || byName(a, b);
      });
    } else if (sort === "year-asc") {
      sorted.sort(function (a, b) {
        return a.year - b.year || byName(a, b);
      });
    } else if (sort === "console") {
      sorted.sort(function (a, b) {
        return (
          a.console.localeCompare(b.console, "pt-BR") || byName(a, b)
        );
      });
    } else {
      sorted.sort(function (a, b) {
        return a.index - b.index;
      });
    }

    var fragment = document.createDocumentFragment();
    for (var i = 0; i < sorted.length; i += 1) {
      fragment.appendChild(sorted[i].element);
    }
    grid.appendChild(fragment);
  }

  function resetAll() {
    search.value = "";
    consoleSelect.value = "";
    genreSelect.value = "";
    sortSelect.value = "recent";
    applySort(true);
    applyFilter();
  }

  search.addEventListener("input", function () {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(applyFilter, 120);
  });

  searchClear.addEventListener("click", function () {
    search.value = "";
    applyFilter();
    search.focus();
  });

  consoleSelect.addEventListener("change", applyFilter);
  genreSelect.addEventListener("change", applyFilter);
  sortSelect.addEventListener("change", function () {
    applySort(true);
    applyFilter();
  });

  resetButton.addEventListener("click", resetAll);
  emptyReset.addEventListener("click", resetAll);

  document.addEventListener("keydown", function (event) {
    var target = event.target;
    var typing =
      target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable);

    if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      search.focus();
      return;
    }

    if (event.key === "Escape" && (target === search || search.value)) {
      search.value = "";
      applyFilter();
      search.blur();
    }
  });

  /* ---------- theme ---------- */

  var root = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: light)");

  function currentTheme() {
    return root.dataset.theme || (media.matches ? "light" : "dark");
  }

  function syncThemeButton() {
    var next = currentTheme() === "dark" ? "claro" : "escuro";
    themeToggle.setAttribute("aria-label", "Ativar tema " + next);
    themeToggle.title = "Ativar tema " + next;
  }

  themeToggle.addEventListener("click", function () {
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try {
      localStorage.setItem("roms-theme", next);
    } catch (error) {
      /* storage disabled */
    }
    syncThemeButton();
  });

  window.addEventListener("storage", function (event) {
    if (event.key !== "roms-theme") return;
    if (event.newValue) {
      root.dataset.theme = event.newValue;
    } else {
      delete root.dataset.theme;
    }
    syncThemeButton();
  });

  if (media.addEventListener) {
    media.addEventListener("change", function () {
      if (!root.dataset.theme) syncThemeButton();
    });
  }

  syncThemeButton();

  /* ---------- back to top ---------- */

  function updateToTop() {
    toTop.classList.toggle("show", window.scrollY > 700);
  }

  window.addEventListener("scroll", updateToTop, { passive: true });
  updateToTop();

  toTop.addEventListener("click", function () {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  /* ---------- broken cover fallback ---------- */

  document.addEventListener(
    "error",
    function (event) {
      var image = event.target;
      if (!(image instanceof HTMLImageElement)) return;
      var cover = image.closest(".rom-cover");
      if (!cover) return;
      cover.classList.add("no-image");
      image.remove();
    },
    true
  );

  applyFilter();
})();
