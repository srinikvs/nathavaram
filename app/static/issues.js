// Public Issues pages: VoterId auto-fill on the add form, and confirm prompts for deletes.
// The server decides everything; this only fills fields and asks before a destructive submit.
(function () {
  "use strict";

  var LOOKUP_DELAY_MS = 350;
  var LOOKUP_URL = "/api/issues/voter-lookup";

  function setLookupMessage(node, text) {
    if (node) {
      node.textContent = text;
    }
  }

  function setReadOnly(field, readOnly) {
    if (field) {
      field.readOnly = readOnly;
    }
  }

  function initIssueForm(form) {
    var voterInput = form.querySelector("[data-voter-id]");
    var nameInput = form.querySelector("[data-name]");
    var mobileInput = form.querySelector("[data-mobile]");
    var message = form.querySelector("[data-lookup-status]");
    var csrfInput = form.querySelector('input[name="csrf_token"]');
    var timer = null;
    var lastLookedUp = null;

    function clearLock() {
      setReadOnly(nameInput, false);
      setReadOnly(mobileInput, false);
    }

    function applyVoter(data, voterId) {
      if (voterInput.value.trim() !== voterId) {
        return; // a newer keystroke has already changed the VoterId
      }
      if (!data.found) {
        clearLock();
        setLookupMessage(message, "No active voter has this VoterId. Type Name and Mobile by hand.");
        return;
      }
      nameInput.value = data.name;
      setReadOnly(nameInput, true);
      if (data.mobile) {
        mobileInput.value = data.mobile;
        setReadOnly(mobileInput, true);
      }
      setLookupMessage(message, "Found: Name and Mobile are filled from the voter record.");
    }

    function lookup() {
      var voterId = voterInput.value.trim();
      if (voterId === "") {
        lastLookedUp = null;
        clearLock();
        setLookupMessage(message, "");
        return;
      }
      if (voterId === lastLookedUp) {
        return;
      }
      lastLookedUp = voterId;
      fetch(LOOKUP_URL, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRF-Token": csrfInput ? csrfInput.value : ""
        },
        body: JSON.stringify({ voter_id: voterId })
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Lookup failed with status " + response.status);
          }
          return response.json();
        })
        .then(function (data) {
          applyVoter(data, voterId);
        })
        .catch(function () {
          setLookupMessage(message, "Lookup is unavailable. Type Name and Mobile by hand.");
        });
    }

    voterInput.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(lookup, LOOKUP_DELAY_MS);
    });
    voterInput.addEventListener("change", lookup);
  }

  function initConfirmForms() {
    var forms = document.querySelectorAll("form[data-confirm]");
    Array.prototype.forEach.call(forms, function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) {
          event.preventDefault();
        }
      });
    });
  }

  var issueForm = document.querySelector("[data-issue-form]");
  if (issueForm) {
    initIssueForm(issueForm);
  }
  initConfirmForms();
})();
