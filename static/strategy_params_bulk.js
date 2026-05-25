(function () {
  function normalizeKey(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  function stripCodeFence(text) {
    return String(text || "")
      .trim()
      .replace(/^```(?:json|javascript|js|yaml|yml)?\s*/i, "")
      .replace(/\s*```$/i, "")
      .trim();
  }

  function flattenJson(value, out = {}) {
    if (Array.isArray(value)) {
      value.forEach((item) => flattenJson(item, out));
      return out;
    }
    if (value && typeof value === "object") {
      Object.entries(value).forEach(([key, child]) => {
        if (child && typeof child === "object" && !Array.isArray(child)) {
          flattenJson(child, out);
        } else {
          out[key] = child;
        }
      });
    }
    return out;
  }

  function parseLooseValue(value) {
    const text = String(value ?? "").trim();
    if (/^["'].*["']$/.test(text) && text.length >= 2) return text.slice(1, -1);
    if (/^(true|false)$/i.test(text)) return text.toLowerCase() === "true" ? "true" : "false";
    if (/^null$/i.test(text)) return "";
    return text.replace(/[,;]$/, "").trim();
  }

  function parseParamText(rawText) {
    const text = stripCodeFence(rawText);
    if (!text) return {};
    try {
      const parsed = JSON.parse(text);
      return flattenJson(parsed);
    } catch {}

    const result = {};
    text.split(/\r?\n/).forEach((line) => {
      const clean = line.trim().replace(/^[-*]\s+/, "");
      if (!clean || clean.startsWith("#") || clean.startsWith("//")) return;
      const match = clean.match(/^["']?([A-Za-z_][\w .-]*)["']?\s*(?:=|:)\s*(.+)$/);
      if (!match) return;
      const key = match[1].trim();
      const value = parseLooseValue(match[2]);
      if (key) result[key] = value;
    });
    return result;
  }

  function valueToInputText(value) {
    if (value === undefined || value === null) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function setFieldValue(field, value) {
    const nextValue = valueToInputText(value);
    if (field.type === "checkbox") {
      field.checked = ["1", "true", "yes", "y", "on"].includes(nextValue.trim().toLowerCase());
    } else {
      field.value = nextValue;
    }
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function applyParamsToFields(params, fields, getKey) {
    const entries = Object.entries(params || {});
    const normalized = new Map();
    entries.forEach(([key, value]) => {
      normalized.set(normalizeKey(key), { key, value });
    });

    const matched = [];
    fields.forEach((field) => {
      const fieldKey = getKey(field);
      const hit = normalized.get(normalizeKey(fieldKey));
      if (!hit) return;
      setFieldValue(field, hit.value);
      matched.push({ fieldKey, sourceKey: hit.key });
    });

    const matchedSources = new Set(matched.map((item) => normalizeKey(item.sourceKey)));
    const unmatched = entries
      .map(([key]) => key)
      .filter((key) => !matchedSources.has(normalizeKey(key)));
    return { matched, unmatched };
  }

  window.StrategyParamPaste = {
    parseParamText,
    applyParamsToFields,
    normalizeKey,
  };
})();
