// Keeps the voter list's search filters and scroll position across a visit to a voter's
// details page, so the browser Back button returns to the same list.
//
// Filters are kept only in sessionStorage. They never go into a URL, a link or a log.
// The search is a POST, which Back cannot replay safely, so the list entry is rewritten to
// GET /voters. Going back to it re-posts the saved filters once. Clear forgets them.
(function (scope) {
  "use strict";

  const STORAGE_KEY = "nathavaram.voterList";
  const CSRF_FIELD = "csrf_token";
  const LIST_PATH = "/voters";
  const FIRST_PAGE = 1;

  // Keeps only the non-empty text filters. The CSRF token is never a filter.
  function filtersFromEntries(entries) {
    const filters = {};
    entries.forEach(([name, value]) => {
      if (name !== CSRF_FIELD && typeof value === "string" && value !== "") {
        filters[name] = value;
      }
    });
    return filters;
  }

  function newContext(filters, page, scrollY) {
    return { filters: filters, page: page, scrollY: scrollY, pending: false, restoreScrollY: null };
  }

  function encodeContext(context) {
    return JSON.stringify(context);
  }

  // Returns a clean context, or null when the stored text is missing or not ours.
  function decodeContext(text) {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      return null;
    }
    if (!parsed || typeof parsed !== "object" || !parsed.filters || typeof parsed.filters !== "object") {
      return null;
    }
    const page = Number.isInteger(parsed.page) && parsed.page >= FIRST_PAGE ? parsed.page : FIRST_PAGE;
    const scrollY = Number.isFinite(parsed.scrollY) && parsed.scrollY >= 0 ? parsed.scrollY : 0;
    const restoreScrollY = Number.isFinite(parsed.restoreScrollY) ? parsed.restoreScrollY : null;
    return {
      filters: filtersFromEntries(Object.entries(parsed.filters)),
      page: page,
      scrollY: scrollY,
      pending: parsed.pending === true,
      restoreScrollY: restoreScrollY,
    };
  }

  // What the list page should do on load. Only a visit that came back from details
  // (pending) changes anything; a fresh visit to /voters stays unfiltered.
  function restorePlan(context) {
    if (!context || !context.pending) {
      return { type: "none" };
    }
    if (Object.keys(context.filters).length > 0) {
      return { type: "submit", filters: context.filters, page: context.page, scrollY: context.scrollY };
    }
    return { type: "scroll", y: context.scrollY };
  }

  const api = {
    filtersFromEntries: filtersFromEntries,
    newContext: newContext,
    encodeContext: encodeContext,
    decodeContext: decodeContext,
    restorePlan: restorePlan,
    STORAGE_KEY: STORAGE_KEY,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (typeof document === "undefined") {
    return;
  }
  scope.VoterListContext = api;

  // Storage can be blocked (private modes). Then nothing is remembered and Back shows the
  // plain list, which is the same behaviour as before this script existed.
  function load() {
    try {
      return decodeContext(sessionStorage.getItem(STORAGE_KEY));
    } catch (error) {
      return null;
    }
  }

  function save(context) {
    try {
      sessionStorage.setItem(STORAGE_KEY, encodeContext(context));
    } catch (error) {
      return;
    }
  }

  function forget() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      return;
    }
  }

  function currentFilters(form) {
    return filtersFromEntries(Array.from(new FormData(form)));
  }

  function postFilters(form, plan) {
    Object.entries(plan.filters).forEach(([name, value]) => {
      const field = form.elements.namedItem(name);
      if (field) {
        field.value = value;
      }
    });
    if (plan.page > FIRST_PAGE) {
      const page = document.createElement("input");
      page.type = "hidden";
      page.name = "page";
      page.value = String(plan.page);
      form.appendChild(page);
    }
    form.submit();
  }

  function initList(form) {
    const stored = load();
    const plan = restorePlan(stored);
    if (plan.type === "submit") {
      // Mark the restore as done before posting, so Back cannot loop on it.
      save({ ...stored, pending: false, restoreScrollY: plan.scrollY });
      postFilters(form, plan);
      return;
    }
    if (plan.type === "scroll") {
      window.scrollTo(0, plan.y);
    } else if (stored && stored.restoreScrollY !== null) {
      window.scrollTo(0, stored.restoreScrollY);
    }
    save(newContext(currentFilters(form), Number(form.dataset.page) || FIRST_PAGE, window.scrollY));
    if (window.location.pathname !== LIST_PATH) {
      // The search result sits at a POST URL. Rewrite this entry so Back can use GET.
      window.history.replaceState(window.history.state, "", LIST_PATH);
    }
  }

  function markLeaving(event) {
    const stored = load() || newContext({}, FIRST_PAGE, 0);
    if (event.target.closest("[data-voter-link]")) {
      save({ ...stored, pending: true, scrollY: window.scrollY });
    } else {
      save({ ...stored, pending: true });
    }
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest) {
      return;
    }
    if (event.target.closest("[data-voter-link], [data-voter-list-return]")) {
      markLeaving(event);
    } else if (event.target.closest("[data-clear-filters]")) {
      forget();
    }
  });

  const listForm = document.querySelector("form[data-voter-filters]");
  if (listForm) {
    initList(listForm);
  }
})(typeof window !== "undefined" ? window : globalThis);
