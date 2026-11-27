/* Quick Message modal for SMS / WhatsApp campaign pages. */
(function () {
  function configFromModal(modal) {
    if (!modal) return null;
    let labels = {};
    try {
      labels = JSON.parse(modal.getAttribute("data-target-labels") || "{}") || {};
    } catch (err) {
      labels = {};
    }
    const channel = modal.getAttribute("data-channel") || "";
    if (!channel) return null;
    return {
      channel,
      channelLabel: modal.getAttribute("data-channel-label") || "",
      csrfToken: modal.getAttribute("data-csrf-token") || "",
      maxPhotos: parseInt(modal.getAttribute("data-max-photos"), 10) || 0,
      maxDailyTimes: parseInt(modal.getAttribute("data-max-daily-times"), 10) || 3,
      targetLabels: labels,
    };
  }

  const modal = document.querySelector("[data-quick-message-modal]");
  const cfg = configFromModal(modal);
  if (!cfg) return;

  const form = document.querySelector("[data-quick-message-form]");
  const openBtn = document.querySelector("[data-open-quick-message]");
  if (!modal || !form || !openBtn) return;

  const targetType = form.querySelector("[data-target-type]");
  const targetValues = form.querySelector("[data-target-values]");
  const matchCount = form.querySelector("[data-match-count]");
  const errorBox = form.querySelector("[data-form-error]");
  const dailyFields = form.querySelector("[data-daily-fields]");
  const submitBtn = form.querySelector("[data-submit-message]");
  const photoInput = form.querySelector("[data-photo-input]");
  const messageBox = form.querySelector('textarea[name="message"]');
  const titleInput = form.querySelector('input[name="title"]');
  const valuesLabel = form.querySelector("[data-target-values-label]");
  if (!targetType || !targetValues || !submitBtn || !messageBox || !titleInput) return;

  const trigger = targetValues.querySelector("[data-target-values-trigger]");
  const summary = targetValues.querySelector("[data-target-values-summary]");
  const menu = targetValues.querySelector("[data-target-values-menu]");
  const optionsRoot = targetValues.querySelector("[data-target-values-options]");
  const selectAllBtn = targetValues.querySelector("[data-select-all]");
  const clearBtn = targetValues.querySelector("[data-clear-selection]");
  if (!trigger || !summary || !menu || !optionsRoot) return;

  function csrfHeaders() {
    const headers = {};
    if (cfg.csrfToken) headers["X-CSRF-Token"] = cfg.csrfToken;
    return headers;
  }

  function showError(text) {
    if (!errorBox) return;
    if (!text) {
      errorBox.hidden = true;
      errorBox.textContent = "";
      return;
    }
    errorBox.hidden = false;
    errorBox.textContent = text;
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function typeLabel() {
    const type = targetType.value;
    return (cfg.targetLabels && cfg.targetLabels[type]) || type;
  }

  function placeholderText() {
    return "Select " + typeLabel() + "...";
  }

  function selectedValues() {
    return Array.from(optionsRoot.querySelectorAll('input[type="checkbox"]'))
      .filter((box) => box.checked)
      .map((box) => box.value);
  }

  function updateSummary() {
    const values = selectedValues();
    if (!values.length) {
      summary.textContent = placeholderText();
    } else if (values.length <= 2) {
      summary.textContent = values.join(", ");
    } else {
      summary.textContent = values.length + " selected";
    }
  }

  function isMenuOpen() {
    return !menu.hidden;
  }

  function setMenuOpen(open) {
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    targetValues.classList.toggle("is-open", open);
  }

  function closeMenu() {
    if (isMenuOpen()) setMenuOpen(false);
  }

  function openMenu() {
    setMenuOpen(true);
  }

  function toggleMenu() {
    setMenuOpen(!isMenuOpen());
  }

  function onSelectionChange() {
    updateSummary();
    refreshCount();
  }

  function setAllChecked(checked) {
    optionsRoot.querySelectorAll('input[type="checkbox"]').forEach((box) => {
      box.checked = checked;
    });
    onSelectionChange();
  }

  function renderOptions(values) {
    clearChildren(optionsRoot);
    (values || []).forEach((value) => {
      const row = document.createElement("label");
      row.className = "multi-select-option";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = value;
      box.addEventListener("change", onSelectionChange);
      const text = document.createElement("span");
      text.textContent = String(value);
      row.appendChild(box);
      row.appendChild(text);
      optionsRoot.appendChild(row);
    });
    updateSummary();
  }

  function updateSubmitLabel() {
    const mode = form.querySelector('input[name="send_mode"]:checked');
    const daily = mode && mode.value === "daily";
    if (daily) {
      submitBtn.textContent =
        cfg.channel === "whatsapp" ? "Schedule WhatsApp Message" : "Schedule SMS";
    } else {
      submitBtn.textContent =
        cfg.channel === "whatsapp" ? "Send WhatsApp Message" : "Send SMS";
    }
    if (dailyFields) dailyFields.hidden = !daily;
  }

  async function loadOptions() {
    const label = typeLabel();
    if (valuesLabel) valuesLabel.textContent = label;
    closeMenu();
    renderOptions([]);
    updateSummary();
    const response = await fetch(
      "/api/campaigns/" + encodeURIComponent(cfg.channel) + "/targets/" + encodeURIComponent(targetType.value),
      { headers: csrfHeaders(), credentials: "same-origin" }
    );
    if (!response.ok) {
      showError("Could not load target options.");
      return;
    }
    const data = await response.json();
    renderOptions(data.options || []);
    await refreshCount();
  }

  async function refreshCount() {
    if (!matchCount) return;
    const values = selectedValues();
    if (!values.length) {
      matchCount.textContent = "Matched voters with mobile: 0";
      return;
    }
    const response = await fetch(
      "/api/campaigns/" + encodeURIComponent(cfg.channel) + "/preview",
      {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, csrfHeaders()),
        credentials: "same-origin",
        body: JSON.stringify({
          target_type: targetType.value,
          target_values: values,
        }),
      }
    );
    if (!response.ok) {
      matchCount.textContent = "Matched voters with mobile: —";
      return;
    }
    const data = await response.json();
    matchCount.textContent =
      "Matched voters with mobile: " + (data.with_mobile || 0);
  }

  function dailyTimes() {
    return Array.from(form.querySelectorAll("[data-daily-times] input"))
      .map((input) => (input.value || "").trim())
      .filter(Boolean)
      .slice(0, cfg.maxDailyTimes || 3);
  }

  async function submitCampaign() {
    showError("");
    const values = selectedValues();
    if (!values.length) {
      showError("Select at least one target value.");
      return;
    }
    if (photoInput && photoInput.files && photoInput.files.length > cfg.maxPhotos) {
      showError("You can attach up to " + cfg.maxPhotos + " photos.");
      return;
    }
    const mode = form.querySelector('input[name="send_mode"]:checked');
    const sendMode = mode ? mode.value : "now";
    if (sendMode === "daily" && !dailyTimes().length) {
      showError("Choose at least one daily send time.");
      return;
    }

    const body = new FormData();
    body.append("title", titleInput.value || "");
    body.append("message", messageBox.value || "");
    body.append("target_type", targetType.value);
    body.append("target_values", JSON.stringify(values));
    body.append("send_mode", sendMode);
    body.append("daily_times", JSON.stringify(dailyTimes()));
    const start = form.querySelector('input[name="start_date"]');
    const end = form.querySelector('input[name="end_date"]');
    if (start && start.value) body.append("start_date", start.value);
    if (end && end.value) body.append("end_date", end.value);
    const active = form.querySelector('input[name="schedule_active"]');
    body.append("schedule_active", active && active.checked ? "true" : "false");
    if (photoInput && photoInput.files) {
      Array.from(photoInput.files).forEach((file) => body.append("photos", file));
    }

    submitBtn.disabled = true;
    try {
      const response = await fetch(
        "/api/campaigns/" + encodeURIComponent(cfg.channel),
        {
          method: "POST",
          headers: csrfHeaders(),
          credentials: "same-origin",
          body: body,
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        showError(data.detail || "Could not send the campaign.");
        return;
      }
      modal.close();
      window.location.reload();
    } catch (err) {
      showError("Network error while sending.");
    } finally {
      submitBtn.disabled = false;
    }
  }

  openBtn.addEventListener("click", function () {
    showError("");
    form.reset();
    const ward = form.querySelector('option[value="ward"]');
    if (ward) {
      ward.selected = true;
      targetType.value = "ward";
    }
    closeMenu();
    updateSubmitLabel();
    loadOptions();
    if (typeof modal.showModal === "function") modal.showModal();
  });

  trigger.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    toggleMenu();
  });

  if (selectAllBtn) {
    selectAllBtn.addEventListener("click", function (event) {
      event.preventDefault();
      setAllChecked(true);
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener("click", function (event) {
      event.preventDefault();
      setAllChecked(false);
    });
  }

  // Escape closes the dropdown first; only a later Escape closes the dialog.
  let suppressDialogCancel = false;

  document.addEventListener("click", function (event) {
    if (!isMenuOpen()) return;
    if (targetValues.contains && targetValues.contains(event.target)) return;
    closeMenu();
  });

  modal.addEventListener("cancel", function (event) {
    if (isMenuOpen() || suppressDialogCancel) {
      event.preventDefault();
      closeMenu();
      suppressDialogCancel = false;
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (!isMenuOpen()) return;
    event.preventDefault();
    event.stopPropagation();
    suppressDialogCancel = true;
    closeMenu();
  });

  targetType.addEventListener("change", loadOptions);
  form.querySelectorAll("[data-send-mode]").forEach((input) => {
    input.addEventListener("change", updateSubmitLabel);
  });
  submitBtn.addEventListener("click", function (event) {
    event.preventDefault();
    submitCampaign();
  });
  form.querySelectorAll("[data-insert]").forEach((btn) => {
    btn.addEventListener("click", function () {
      const sym = btn.getAttribute("data-insert") || "";
      const start = messageBox.selectionStart || messageBox.value.length;
      const end = messageBox.selectionEnd || start;
      messageBox.value =
        messageBox.value.slice(0, start) + sym + messageBox.value.slice(end);
      messageBox.focus();
      const pos = start + sym.length;
      messageBox.setSelectionRange(pos, pos);
    });
  });

  updateSummary();
  updateSubmitLabel();
})();
