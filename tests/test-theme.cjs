// Exercise the site's rendered bootstrap against the actual bundled Chirpy code.
// Node's VM supplies a minimal DOM/storage; no browser profile is modified.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "_site/index.html"), "utf8");
const match = html.match(/<script\b[^>]*\bid=["']default-theme["'][^>]*>([\s\S]*?)<\/script>/);
assert.ok(match, "Build the site first; its dark-default bootstrap must be present");
const bootstrap = match[1];
const chirpy = fs.readFileSync(path.join(root, "_site/assets/js/dist/theme.min.js"), "utf8");
const marker = "matthetherington.github.io:theme-initialized";

function load({ storage = new Map(), systemDark = false } = {}) {
  const attributes = new Set();
  const element = {
    dataset: {},
    toggleAttribute(name, value) {
      if (value) attributes.add(name);
      else attributes.delete(name);
    }
  };
  const listeners = [];
  const media = {
    matches: systemDark,
    addEventListener(event, callback) {
      assert.equal(event, "change");
      listeners.push(callback);
    }
  };
  const context = vm.createContext({
    document: { documentElement: element },
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); }
    },
    window: {
      matchMedia() { return media; },
      postMessage() {}
    }
  });
  vm.runInContext(chirpy, context);
  vm.runInContext(bootstrap, context);
  return {
    storage,
    element,
    attributes,
    isToggleable: () => vm.runInContext("Theme.isToggleable", context),
    select(mode) {
      assert.ok(["light", "dark", "system"].includes(mode));
      vm.runInContext(`Theme.update("${mode}")`, context);
    },
    changeSystem(dark) {
      media.matches = dark;
      for (const callback of listeners) callback();
    }
  };
}

test("first visit defaults to dark even on a light system", () => {
  const page = load({ systemDark: false });
  assert.equal(page.element.dataset.bsTheme, "dark");
  assert.equal(page.storage.get("theme"), "dark");
  assert.equal(page.storage.get(marker), "1");
  assert.ok(page.isToggleable());
});

test("first visit also stays dark on a dark system", () => {
  assert.equal(load({ systemDark: true }).element.dataset.bsTheme, "dark");
});

test("visitor can select light and keep it across page loads", () => {
  const first = load();
  first.select("light");
  assert.equal(first.element.dataset.bsTheme, "light");
  const next = load({ storage: first.storage, systemDark: true });
  assert.equal(next.element.dataset.bsTheme, "light");
  assert.ok(next.isToggleable());
});

test("an existing light preference is not replaced by the new default", () => {
  const page = load({ storage: new Map([["theme", "light"]]), systemDark: true });
  assert.equal(page.element.dataset.bsTheme, "light");
  assert.equal(page.storage.get(marker), "1");
});

test("an existing dark preference is retained", () => {
  assert.equal(load({ storage: new Map([["theme", "dark"]]) }).element.dataset.bsTheme, "dark");
});

test("System remains selected across navigation, including a light system", () => {
  const first = load();
  first.select("system");
  assert.equal(first.element.dataset.bsTheme, "light");
  assert.equal(first.storage.has("theme"), false);
  assert.equal(first.storage.get(marker), "1");
  const next = load({ storage: first.storage, systemDark: false });
  assert.equal(next.element.dataset.bsTheme, "light");
  next.changeSystem(true);
  assert.equal(next.element.dataset.bsTheme, "dark");
});

test("explicit dark does not change when the system preference changes", () => {
  const page = load();
  page.changeSystem(true);
  page.changeSystem(false);
  assert.equal(page.element.dataset.bsTheme, "dark");
});

test("visitor can switch from light back to dark", () => {
  const page = load();
  page.select("light");
  page.select("dark");
  assert.equal(load({ storage: page.storage }).element.dataset.bsTheme, "dark");
});

test("the bootstrap adds no uncaught error if storage is blocked", () => {
  const context = vm.createContext({
    localStorage: { getItem() { throw new Error("Storage blocked"); } }
  });
  assert.doesNotThrow(() => vm.runInContext(bootstrap, context));
});
