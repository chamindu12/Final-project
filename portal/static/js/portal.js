/* Progressive enhancements. Server-rendered forms and downloads work without JS. */
(() => {
  "use strict";

  const resetForms = () => document.querySelectorAll("form[data-submit-form]").forEach((form) => {
    form.removeAttribute("aria-busy");
    form.querySelectorAll("button[type='submit']").forEach((button) => {
      button.disabled = false;
      if (button.dataset.originalLabel) button.innerHTML = button.dataset.originalLabel;
    });
  });
  // Re-enable controls when a page is restored from the browser's back/forward cache.
  window.addEventListener("pageshow", resetForms);
  document.querySelectorAll("form[data-submit-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll("button[type='submit']").forEach((button) => {
        button.dataset.originalLabel = button.innerHTML;
        button.disabled = true;
        if (button.dataset.busyLabel) button.textContent = button.dataset.busyLabel;
      });
    });
  });

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const input = document.getElementById(button.getAttribute("aria-controls"));
    if (!input) return;
    button.hidden = false;
    button.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "Hide" : "Show";
      button.setAttribute("aria-label", show ? "Hide password" : "Show password");
      button.setAttribute("aria-pressed", String(show));
    });
  });

  const note = document.querySelector("[data-note-input]");
  const counter = document.querySelector("[data-note-count]");
  if (note && counter) {
    const update = () => { counter.textContent = `${note.value.length} / ${note.maxLength} characters`; };
    note.addEventListener("input", update);
    update();
  }

  document.querySelectorAll("time[data-local-time]").forEach((element) => {
    const date = new Date(element.dateTime);
    if (!Number.isNaN(date.getTime())) {
      element.textContent = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short"
      }).format(date);
      element.title = `${date.toISOString()} (UTC); displayed in your local time`;
    }
  });

  const tools = document.querySelector("[data-file-tools]");
  if (tools) {
    const list = document.querySelector("[data-file-list]");
    const search = document.querySelector("[data-file-search]");
    const sort = document.querySelector("[data-file-sort]");
    const result = document.querySelector("[data-file-results]");
    const empty = document.querySelector("[data-file-empty]");
    const rows = Array.from(list.querySelectorAll("tr[data-file-name]"));
    const update = () => {
      const term = search.value.trim().toLocaleLowerCase();
      rows.sort((a, b) => {
        if (sort.value === "size") return Number(a.dataset.fileBytes) - Number(b.dataset.fileBytes);
        if (sort.value === "size-desc") return Number(b.dataset.fileBytes) - Number(a.dataset.fileBytes);
        if (sort.value === "recent") return Date.parse(b.dataset.fileModified) - Date.parse(a.dataset.fileModified);
        const order = a.dataset.fileName.localeCompare(b.dataset.fileName, undefined, {numeric: true, sensitivity: "base"});
        return sort.value === "name-desc" ? -order : order;
      });
      let visible = 0;
      rows.forEach((row) => {
        row.hidden = !row.dataset.fileName.toLocaleLowerCase().includes(term);
        if (!row.hidden) visible += 1;
        list.append(row);
      });
      result.textContent = `${visible} of ${rows.length} files shown`;
      empty.hidden = visible > 0;
      list.closest(".table-wrap").hidden = visible === 0;
    };
    tools.hidden = false;
    result.hidden = false;
    search.addEventListener("input", update);
    sort.addEventListener("change", update);
    document.querySelector("[data-file-clear]").addEventListener("click", () => {
      search.value = "";
      update();
      search.focus();
    });
    update();
  }

  const upload = document.querySelector("[data-upload-form]");
  if (upload) {
    const input = upload.querySelector("input[type=file]");
    const selection = upload.querySelector("[data-file-selection]");
    const feedback = upload.querySelector("[data-upload-feedback]");
    const progress = upload.querySelector("[data-upload-progress]");
    const button = upload.querySelector("button[type=submit]");
    const signIn = upload.querySelector("[data-upload-signin]");
    const reload = upload.querySelector("[data-upload-reload]");
    let pending = false;
    const message = (text, error = false) => {
      feedback.hidden = false;
      feedback.textContent = text;
      feedback.dataset.error = String(error);
      feedback.setAttribute("role", error ? "alert" : "status");
    };
    input.addEventListener("change", () => {
      input.setCustomValidity("");
      feedback.hidden = true;
      signIn.hidden = true;
      reload.hidden = true;
      const file = input.files[0];
      selection.textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KiB selected` : "No file selected.";
      if (file && file.size >= Number(upload.dataset.maxBytes)) {
        input.setCustomValidity("This file is too large for the request limit. Choose a smaller file.");
        message(input.validationMessage, true);
      }
    });
    upload.addEventListener("submit", (event) => {
      if (!window.XMLHttpRequest || !window.FormData) return;
      event.preventDefault();
      if (pending || !upload.reportValidity()) return;
      pending = true;
      button.disabled = true;
      input.disabled = false;
      upload.setAttribute("aria-busy", "true");
      signIn.hidden = true;
      reload.hidden = true;
      progress.hidden = false;
      progress.value = 0;
      message("Sending your file…");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", upload.action);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.timeout = 60000;
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          progress.value = Math.round(event.loaded / event.total * 100);
          message(progress.value === 100 ? "Upload sent. Waiting for the server to save the file…" : `Sending your file… ${progress.value}%`);
        }
      });
      const finish = () => {
        pending = false;
        button.disabled = false;
        input.disabled = false;
        upload.removeAttribute("aria-busy");
        progress.hidden = true;
      };
      xhr.addEventListener("load", () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* Gateway errors can be HTML. */ }
        if (xhr.status === 201 && data.redirect === "/#files") {
          message("File saved. Updating your file list…");
          window.location.assign(data.redirect);
          return;
        }
        const defaults = {
          400: "The request could not be accepted. Reload this page and try again.",
          401: "Your session has expired. Sign in again before uploading.",
          403: "You do not have access to upload from this connection.",
          409: "A file with this name already exists. Your existing file is safe. Rename your file and try again.",
          413: "The upload exceeds the request size limit. Choose a smaller file.",
          429: "Too many requests. Wait a moment before trying again."
        };
        const serverError = typeof data.error === "string" ? data.error : "";
        message(xhr.status === 401 ? defaults[401] : serverError || defaults[xhr.status] || "The upload could not be confirmed. Check the file list before trying again.", true);
        signIn.hidden = xhr.status !== 401;
        reload.hidden = xhr.status !== 400;
        finish();
      });
      const uncertain = () => {
        message("The upload could not be confirmed. Check the file list before trying again.", true);
        finish();
      };
      xhr.addEventListener("error", uncertain);
      xhr.addEventListener("timeout", uncertain);
      xhr.addEventListener("abort", uncertain);
      // Construct before disabling the file input so it remains in the request.
      const data = new FormData(upload);
      input.disabled = true;
      xhr.send(data);
    });
  }

  document.querySelectorAll("[data-test-download]").forEach((link) => {
    link.addEventListener("click", () => {
      const status = document.querySelector("[data-download-status]");
      status.hidden = false;
      status.textContent = "Download requested. Check your browser's downloads for progress or completion.";
    });
  });

  document.querySelectorAll(".workspace-nav a, .guest-nav a").forEach((link) => {
    link.addEventListener("click", () => {
      document.querySelectorAll(".workspace-nav a, .guest-nav a").forEach((other) => other.removeAttribute("aria-current"));
      link.setAttribute("aria-current", "location");
    });
  });
})();
