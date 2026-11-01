// Client-side filtering and sorting. Filters are mirrored in the URL so a view can be shared.
(() => {
  const form = document.getElementById("filters");
  const list = document.getElementById("jobs");
  const count = document.getElementById("count");
  const empty = document.getElementById("empty");
  const cards = Array.from(list.children);
  const text = new Map(cards.map((card) => [card, card.textContent.toLowerCase()]));
  const names = ["q", "board", "state", "area", "type", "posted", "remote", "sort"];
  const day = 86400000;
  const training = ["Internship", "Fellowship"];

  const value = (name) => {
    const field = form.elements[name];
    return field.type === "checkbox" ? (field.checked ? "1" : "") : field.value;
  };

  function readUrl() {
    const params = new URLSearchParams(location.search);
    for (const name of names) {
      const field = form.elements[name];
      if (!params.has(name)) continue;
      if (field.type === "checkbox") field.checked = params.get(name) === "1";
      else field.value = params.get(name);
    }
  }

  function writeUrl() {
    const params = new URLSearchParams();
    for (const name of names) {
      const v = value(name);
      if (v && !(name === "sort" && v === "newest")) params.set(name, v);
    }
    const query = params.toString();
    history.replaceState(null, "", query ? `?${query}` : location.pathname);
  }

  function matches(card, f) {
    const d = card.dataset;
    if (f.board && d.board !== f.board) return false;
    if (f.state && d.state !== f.state) return false;
    if (f.area && d.area !== f.area) return false;
    if (f.remote && !d.remote) return false;
    if (f.posted && !(d.posted && Date.now() - Date.parse(d.posted) <= Number(f.posted) * day)) return false;
    if (f.type) {
      const types = d.types ? d.types.split("|") : [];
      if (f.type === "no-training" ? types.some((t) => training.includes(t)) : !types.includes(f.type)) return false;
    }
    return f.terms.every((term) => text.get(card).includes(term));
  }

  function filter() {
    const f = Object.fromEntries(names.map((name) => [name, value(name)]));
    f.terms = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const card of cards) {
      card.hidden = !matches(card, f);
      if (!card.hidden) shown += 1;
    }
    const total = cards.length.toLocaleString();
    count.textContent = shown === cards.length ? `${total} jobs` : `${shown.toLocaleString()} of ${total} jobs`;
    empty.hidden = shown > 0;
  }

  function sort() {
    const by = value("sort");
    const keys = {
      newest: (c) => c.dataset.order.padStart(6, "0"),
      deadline: (c) => c.dataset.deadline || "9999",
      employer: (c) => c.dataset.org || "~",
    };
    const key = keys[by] || keys.newest;
    list.append(...[...cards].sort((a, b) => key(a).localeCompare(key(b)) || a.dataset.order - b.dataset.order));
  }

  function update(event) {
    if (!event || event.target.name === "sort") sort();
    filter();
    writeUrl();
  }

  readUrl();
  sort();
  filter();
  form.addEventListener("input", update);
  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("reset", () => setTimeout(() => update()));
  document.getElementById("empty-reset").addEventListener("click", () => form.reset());
})();
