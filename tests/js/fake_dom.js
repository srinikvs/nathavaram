"use strict";
// A small DOM stand-in so the real app/static scripts (app.js and voter-list-context.js) can run
// under Node without a browser. It supports only the selectors, events and window APIs those
// scripts use, and it models capture, target and bubble phases with stopPropagation.
// Synthetic test pages only: no real voter data.

const fs = require("fs");
const nodePath = require("path");
const vm = require("vm");

const STATIC_DIR = nodePath.resolve(__dirname, "..", "..", "app", "static");
const SCRIPT_FILES = ["app.js", "voter-list-context.js"];
const STORAGE_KEY = "nathavaram.voterList";
const SIMPLE_SELECTOR = /^([a-z]*)((?:\[[a-z-]+\])*)$/;
const FILTER_FIELDS = ["q", "booth", "ward", "mobile", "voter_id", "is_local"];

// Supports "tag", "[attr]" and "tag[attr]" parts, joined by commas. Anything else never matches.
function matchesCompound(element, compound) {
  const match = SIMPLE_SELECTOR.exec(compound.trim());
  if (!match || (!match[1] && !match[2])) {
    return false;
  }
  if (match[1] && element.tag !== match[1]) {
    return false;
  }
  const names = Array.from(match[2].matchAll(/\[([a-z-]+)\]/g), (found) => found[1]);
  return names.every((name) => Object.prototype.hasOwnProperty.call(element.attrs, name));
}

function matches(element, selector) {
  return selector.split(",").some((part) => matchesCompound(element, part));
}

function descendants(root) {
  const found = [];
  const walk = (node) => {
    node.children.forEach((child) => {
      found.push(child);
      walk(child);
    });
  };
  walk(root);
  return found;
}

function addListener(holder, type, fn, options) {
  const capture = options === true || Boolean(options && options.capture);
  holder.listeners[type] = holder.listeners[type] || [];
  holder.listeners[type].push({ fn: fn, capture: capture });
}

function runListeners(holder, event, phase) {
  const wantCapture = phase === "capture";
  (holder.listeners[event.type] || [])
    .filter((listener) => listener.capture === wantCapture)
    .forEach((listener) => {
      if (!event.stopped) {
        listener.fn(event);
      }
    });
}

function makeEvent(type, target, init) {
  return Object.assign(
    {
      type: type,
      target: target,
      button: 0,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
      altKey: false,
      persisted: false,
      stopped: false,
      defaultPrevented: false,
      stopPropagation() {
        this.stopped = true;
      },
      preventDefault() {
        this.defaultPrevented = true;
      },
    },
    init || {},
  );
}

function createElement(tag, attrs) {
  const element = {
    tag: tag,
    attrs: Object.assign({}, attrs || {}),
    parent: null,
    children: [],
    listeners: {},
    name: attrs && attrs.name !== undefined ? attrs.name : "",
    value: attrs && attrs.value !== undefined ? attrs.value : "",
    checked: false,
    hidden: false,
    textContent: "",
    get dataset() {
      const out = {};
      Object.keys(this.attrs).forEach((key) => {
        if (key.startsWith("data-")) {
          out[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = this.attrs[key];
        }
      });
      return out;
    },
    get elements() {
      const form = this;
      return {
        namedItem(name) {
          return descendants(form).find((node) => node.tag === "input" && node.name === name) || null;
        },
      };
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    },
    closest(selector) {
      for (let node = this; node; node = node.parent) {
        if (matches(node, selector)) {
          return node;
        }
      }
      return null;
    },
    querySelector(selector) {
      return descendants(this).find((node) => matches(node, selector)) || null;
    },
    querySelectorAll(selector) {
      return descendants(this).filter((node) => matches(node, selector));
    },
    appendChild(child) {
      child.parent = this;
      this.children.push(child);
      return child;
    },
    addEventListener(type, fn, options) {
      addListener(this, type, fn, options);
    },
    formEntries() {
      return descendants(this)
        .filter((node) => node.tag === "input" && node.name)
        .map((node) => [node.name, node.value]);
    },
    submit() {
      this.submitted = this.submitted || [];
      this.submitted.push(this.formEntries());
    },
  };
  return element;
}

function createDocument(body) {
  return {
    body: body,
    listeners: {},
    addEventListener(type, fn, options) {
      addListener(this, type, fn, options);
    },
    querySelector(selector) {
      return body.querySelector(selector);
    },
    querySelectorAll(selector) {
      return body.querySelectorAll(selector);
    },
    getElementById() {
      return null;
    },
    createElement(tag) {
      return createElement(tag, {});
    },
  };
}

function createWindow(pathname, scrollY) {
  const win = {
    location: { pathname: pathname },
    scrollY: scrollY,
    scrollCalls: [],
    listeners: {},
    history: {
      state: null,
      lastUrl: null,
      replaceState(state, title, url) {
        win.location.pathname = url;
        win.history.lastUrl = url;
      },
    },
    scrollTo(x, y) {
      win.scrollY = y;
      win.scrollCalls.push(y);
    },
    addEventListener(type, fn, options) {
      addListener(win, type, fn, options);
    },
  };
  return win;
}

function createSessionStorage(store) {
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
  };
}

class FormDataStub {
  constructor(form) {
    this.pairs = form.formEntries();
  }

  [Symbol.iterator]() {
    return this.pairs[Symbol.iterator]();
  }
}

// The list page: bulk selection region with one row, a filter form, and the Clear control.
function listBody(options) {
  const opts = options || {};
  const filters = opts.filters || {};
  const voterId = opts.voterId || "ZZZ50000001";
  const body = createElement("body", {});
  const rowClicks = [];

  const bulk = body.appendChild(createElement("section", { "data-bulk": "" }));
  const bar = bulk.appendChild(createElement("p", { "data-bulk-bar": "" }));
  bulk.appendChild(createElement("span", { "data-bulk-count": "" }));

  const form = body.appendChild(
    createElement("form", {
      method: "post",
      action: "/voters/search",
      "data-voter-filters": "",
      "data-page": String(opts.page || 1),
    }),
  );
  form.appendChild(createElement("input", { name: "csrf_token", value: "synthetic-token" }));
  FILTER_FIELDS.forEach((name) => {
    form.appendChild(createElement("input", { name: name, value: filters[name] || "" }));
  });
  const clear = body.appendChild(createElement("a", { href: "/voters", "data-clear-filters": "" }));

  const table = bulk.appendChild(createElement("table", {}));
  const row = table.appendChild(createElement("tr", {}));
  row.addEventListener("click", () => rowClicks.push("row"));
  const checkbox = row.appendChild(
    createElement("input", { type: "checkbox", name: "voter_ids", value: voterId, "data-row-select": "" }),
  );
  const cell = row.appendChild(createElement("td", {}));
  const link = cell.appendChild(createElement("a", { href: "/voters/" + voterId, "data-voter-link": "" }));
  link.textContent = voterId;

  return { body, form, link, checkbox, bar, clear, rowClicks };
}

// The details page: only the Back to list control matters for the list context.
function detailsBody(options) {
  const voterId = (options && options.voterId) || "ZZZ50000001";
  const body = createElement("body", {});
  const back = body.appendChild(createElement("a", { href: "/voters", "data-voter-list-return": "" }));
  back.textContent = "Back to list";
  return { body, back, voterId };
}

// Runs the real static scripts against a page. `store` plays the part of sessionStorage and
// survives across openPage calls, so one call per navigation models a page load.
function openPage(tree, options) {
  const opts = options || {};
  const store = opts.store || new Map();
  const doc = createDocument(tree.body);
  const win = createWindow(opts.path || "/voters", opts.scrollY || 0);
  const sandbox = {
    document: doc,
    window: win,
    sessionStorage: createSessionStorage(store),
    FormData: FormDataStub,
  };
  const context = vm.createContext(sandbox);
  SCRIPT_FILES.forEach((file) => {
    const source = fs.readFileSync(nodePath.join(STATIC_DIR, file), "utf8");
    vm.runInContext(source, context, { filename: file });
  });
  return { tree: tree, doc: doc, win: win, store: store };
}

// Capture listeners run from the document down to the target, then bubble listeners run from
// the target up to the document. stopPropagation() ends the walk, as in a browser.
function dispatch(page, target, type, init) {
  const event = makeEvent(type, target, init);
  const chain = [];
  for (let node = target; node; node = node.parent) {
    chain.push(node);
  }
  const capturePath = [page.doc].concat(chain.slice().reverse());
  const bubblePath = chain.concat([page.doc]);
  [["capture", capturePath], ["bubble", bubblePath]].forEach(([phase, nodes]) => {
    nodes.forEach((node) => {
      if (!event.stopped) {
        runListeners(node, event, phase);
      }
    });
  });
  return event;
}

function click(page, target, init) {
  return dispatch(page, target, "click", init);
}

function submit(page, form) {
  return dispatch(page, form, "submit", {});
}

function windowEvent(page, type, init) {
  const event = makeEvent(type, null, init);
  runListeners(page.win, event, "bubble");
  return event;
}

function saved(store) {
  const text = store.get(STORAGE_KEY);
  return text === undefined ? null : JSON.parse(text);
}

module.exports = {
  STORAGE_KEY: STORAGE_KEY,
  listBody: listBody,
  detailsBody: detailsBody,
  openPage: openPage,
  click: click,
  submit: submit,
  windowEvent: windowEvent,
  saved: saved,
};
