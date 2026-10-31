// Tooltips for the Trends charts, on hover and keyboard focus. Values are also in each chart's table view.
(() => {
  const tip = document.getElementById("tooltip");
  const marks = document.querySelectorAll("[data-value]");

  function show(mark, x, y) {
    tip.replaceChildren();
    const value = document.createElement("strong");
    value.textContent = mark.dataset.value;
    const label = document.createElement("span");
    label.textContent = mark.dataset.label;
    tip.append(value, label);
    if (mark.dataset.note) {
      const note = document.createElement("span");
      note.className = "tooltip-note";
      note.textContent = mark.dataset.note;
      tip.append(note);
    }
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    const left = Math.min(Math.max(8, x - box.width / 2), window.innerWidth - box.width - 8);
    const top = y - box.height - 12 < 8 ? y + 16 : y - box.height - 12;
    tip.style.left = `${left + window.scrollX}px`;
    tip.style.top = `${top + window.scrollY}px`;
    mark.classList.add("active");
  }

  function hide(mark) {
    tip.hidden = true;
    mark.classList.remove("active");
  }

  for (const mark of marks) {
    mark.addEventListener("pointermove", (event) => show(mark, event.clientX, event.clientY));
    mark.addEventListener("pointerleave", () => hide(mark));
    mark.addEventListener("focus", () => {
      const box = mark.getBoundingClientRect();
      show(mark, box.left + box.width / 2, box.top);
    });
    mark.addEventListener("blur", () => hide(mark));
  }
})();
