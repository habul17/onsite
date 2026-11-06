// A small code editor on a plain textarea: line numbers, Tab to indent, and Enter keeps the indent.
// It works offline and needs no build step.
"use strict";

const INDENT = "    ";

function codeEditor(textarea, gutter, { onSubmit } = {}) {
  let shownLines = 0;
  // Tab indents, so keyboard users press Esc first to Tab out of the editor.
  let escaped = false;

  function renumber() {
    const n = textarea.value.split("\n").length;
    if (n !== shownLines) {
      gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
      shownLines = n;
    }
    gutter.scrollTop = textarea.scrollTop;
  }

  function insert(text) {
    // execCommand keeps the browser's undo history. Older or stricter browsers fall back to setRangeText.
    if (!document.execCommand("insertText", false, text)) {
      textarea.setRangeText(text, textarea.selectionStart, textarea.selectionEnd, "end");
      textarea.dispatchEvent(new Event("input"));
    }
  }

  function lineStart(pos) {
    return textarea.value.lastIndexOf("\n", pos - 1) + 1;
  }

  function shiftLines(outdent) {
    const { value, selectionStart: start, selectionEnd: end } = textarea;
    const from = lineStart(start);
    // A selection that ends at the start of a line doesn't include that line.
    const last = end > start && value[end - 1] === "\n" ? end - 1 : end;
    let to = value.indexOf("\n", last);
    if (to === -1) to = value.length;

    const lines = value.slice(from, to).split("\n");
    const changed = lines.map((line) => (outdent ? line.replace(/^( {1,4}|\t)/, "") : INDENT + line)).join("\n");
    if (changed === value.slice(from, to)) return;
    textarea.setSelectionRange(from, to);
    insert(changed);
    if (start === end && !outdent) textarea.setSelectionRange(start + INDENT.length, start + INDENT.length);
    else textarea.setSelectionRange(from, from + changed.length);
  }

  function newLine() {
    const { value, selectionStart: start } = textarea;
    const before = value.slice(lineStart(start), start);
    let indent = before.match(/^[ \t]*/)[0];
    // One level deeper after a line that opens a block, like "def f():" or "if (x) {".
    if (/[:{[(]\s*$/.test(before)) indent += INDENT;
    insert("\n" + indent);
  }

  function backspaceIndent() {
    // Inside the leading spaces, Backspace removes one indent level instead of one space.
    const { value, selectionStart: start, selectionEnd: end } = textarea;
    if (start !== end) return false;
    const before = value.slice(lineStart(start), start);
    if (!before || /[^ ]/.test(before)) return false;
    const remove = before.length % INDENT.length || INDENT.length;
    textarea.setSelectionRange(start - remove, start);
    insert("");
    return true;
  }

  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      escaped = true;
      return;
    }
    if (e.key === "Tab" && !escaped && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      const { selectionStart: start, selectionEnd: end } = textarea;
      if (e.shiftKey || start !== end) shiftLines(e.shiftKey);
      else insert(INDENT);
    } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      if (onSubmit) onSubmit();
    } else if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.isComposing) {
      e.preventDefault();
      newLine();
    } else if (e.key === "Backspace" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (backspaceIndent()) e.preventDefault();
    }
    escaped = false;
  });
  textarea.addEventListener("input", renumber);
  textarea.addEventListener("scroll", () => (gutter.scrollTop = textarea.scrollTop));

  renumber();
  return {
    get value() {
      return textarea.value;
    },
    set value(text) {
      textarea.value = text;
      renumber();
    },
    focus() {
      textarea.focus();
    },
  };
}

window.codeEditor = codeEditor;
