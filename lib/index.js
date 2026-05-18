"use strict";

const controls = require("@jupyter-widgets/controls");
const packageJson = require("../package.json");

const MODULE_NAME = "aipywidgets";
const MODULE_VERSION = packageJson.version;
const BELOW_BUBBLE_OFFSET_X = 20;

class AssistLayerModel extends controls.BoxModel {
  defaults() {
    return {
      ...super.defaults(),
      _model_name: "AssistLayerModel",
      _view_name: "AssistLayerView",
      _model_module: MODULE_NAME,
      _view_module: MODULE_NAME,
      _model_module_version: MODULE_VERSION,
      _view_module_version: MODULE_VERSION,
      anchor_dom_class: "",
      form_dom_class: "",
      placement: "right"
    };
  }
}

class AssistLayerView extends controls.BoxView {
  _bubbleMetrics(bubble) {
    const width = bubble.offsetWidth || parseFloat(window.getComputedStyle(bubble).width) || 320;
    return {
      width
    };
  }

  _primaryAnchorRect(anchor) {
    const control = anchor.querySelector("textarea, input, select");
    if (!control) {
      throw new Error("Assist anchor is missing an input control");
    }
    return control.getBoundingClientRect();
  }

  render() {
    super.render();
    this._raf = null;
    this._handleWindowResize = () => this.schedulePosition();
    this.el.classList.add("aipy-assist-layer-root");
    this.el.style.position = "relative";
    this.el.style.overflow = "visible";
    this.listenTo(this.model, "change:anchor_dom_class", this.schedulePosition);
    this.listenTo(this.model, "change:form_dom_class", this.schedulePosition);
    this.listenTo(this.model, "change:placement", this.schedulePosition);
    window.addEventListener("resize", this._handleWindowResize);
    if (typeof ResizeObserver !== "undefined") {
      this._resizeObserver = new ResizeObserver(() => this.schedulePosition());
      this._resizeObserver.observe(this.el);
    }
    this.displayed.then(() => this.schedulePosition());
  }

  update() {
    const value = super.update();
    this.schedulePosition();
    return value;
  }

  remove() {
    window.removeEventListener("resize", this._handleWindowResize);
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
    if (this._raf !== null) {
      window.cancelAnimationFrame(this._raf);
      this._raf = null;
    }
    return super.remove();
  }

  schedulePosition() {
    if (this._raf !== null) {
      window.cancelAnimationFrame(this._raf);
    }
    this._raf = window.requestAnimationFrame(() => {
      this._raf = null;
      this.positionBubble();
    });
  }

  positionBubble() {
    const bubble = this.el.querySelector(".aipy-assist-bubble-wrap");
    const formClass = this.model.get("form_dom_class");
    const anchorClass = this.model.get("anchor_dom_class");
    const placement = this.model.get("placement") || "right";
    if (!bubble || !formClass || !anchorClass) {
      return;
    }
    const root = this.el.closest(`.${formClass}`);
    if (!root) {
      return;
    }
    const anchor = root.querySelector(`.${anchorClass}`);
    if (!anchor) {
      return;
    }
    const layerRect = this.el.getBoundingClientRect();
    const anchorRect = this._primaryAnchorRect(anchor);
    const { width: bubbleWidth } = this._bubbleMetrics(bubble);
    bubble.classList.toggle("aipy-assist-bubble-below", placement === "below");
    if (placement === "below") {
      const left = anchorRect.left - layerRect.left + BELOW_BUBBLE_OFFSET_X;
      const anchorX = Math.min(anchorRect.left + 40, anchorRect.left + anchorRect.width / 2);
      const arrowLeft = Math.max(
        24,
        Math.min(bubbleWidth - 24, anchorX - anchorRect.left - BELOW_BUBBLE_OFFSET_X)
      );
      bubble.style.left = `${left}px`;
      bubble.style.top = `${anchorRect.bottom - layerRect.top + 12}px`;
      bubble.style.setProperty("--aipy-assist-arrow-left", `${arrowLeft}px`);
      return;
    }
    bubble.style.removeProperty("--aipy-assist-arrow-left");
    bubble.style.left = `${anchorRect.right - layerRect.left + 12}px`;
    bubble.style.top = `${anchorRect.top - layerRect.top}px`;
  }
}

module.exports = {
  AssistLayerModel,
  AssistLayerView
};
