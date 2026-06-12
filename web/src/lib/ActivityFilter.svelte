<script>
  import { ACTIVITY_CATEGORIES } from "./api.js";

  let { selected = $bindable() } = $props();

  function toggle(key) {
    selected = selected.includes(key)
      ? selected.filter((k) => k !== key)
      : [...selected, key];
  }

  const allOn = $derived(selected.length === ACTIVITY_CATEGORIES.length);
  function toggleAll() {
    selected = allOn ? [] : ACTIVITY_CATEGORIES.map((c) => c.key);
  }
</script>

<div class="chips">
  <button type="button" class="chip all" class:active={allOn} onclick={toggleAll}>
    {allOn ? "Clear" : "All"}
  </button>
  {#each ACTIVITY_CATEGORIES as c}
    <button
      type="button"
      class="chip"
      class:active={selected.includes(c.key)}
      onclick={() => toggle(c.key)}
    >
      {c.label}
    </button>
  {/each}
</div>
