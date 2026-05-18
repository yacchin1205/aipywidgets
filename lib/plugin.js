"use strict";

const base = require("@jupyter-widgets/base");
const packageJson = require("../package.json");
const widgetExports = require("./index");

const plugin = {
  id: "aipywidgets:plugin",
  requires: [base.IJupyterWidgetRegistry],
  activate: (app, registry) => {
    registry.registerWidget({
      name: "aipywidgets",
      version: packageJson.version,
      exports: widgetExports
    });
  },
  autoStart: true
};

module.exports = plugin;
