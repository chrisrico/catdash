<script>
  // feeders/onAssignFeeder are optional (Trends only): with more than one cat,
  // each feeder gets a picker for whose food it dispenses ("" = shared).
  let { pets, petId = $bindable(), range = $bindable(), feeders = [], onAssignFeeder } = $props();
</script>

<section class="controls">
  <label class="ctl">
    <span>Cat</span>
    <select bind:value={petId}>
      {#if pets.length > 1}
        <option value="">All cats</option>
      {/if}
      {#each pets as p}
        <option value={p.id}>{p.name || p.id}</option>
      {/each}
    </select>
  </label>
  <label class="ctl">
    <span>Range</span>
    <select bind:value={range}>
      <option value="30">Last 30 days</option>
      <option value="90">Last 90 days</option>
      <option value="180">Last 6 months</option>
      <option value="365">Last year</option>
      <option value="all">All time</option>
    </select>
  </label>
  {#if pets.length > 1 && onAssignFeeder}
    {#each feeders as f (f.id)}
      <label class="ctl">
        <span>{f.name || "Feeder"} feeds</span>
        <select
          value={f.pet_id ?? ""}
          onchange={async (e) => {
            const el = e.currentTarget;
            // A failed save leaves `feeders` unchanged, so Svelte won't reset the
            // DOM value itself; put the saved assignment back by hand.
            if (!(await onAssignFeeder(f.id, el.value))) el.value = f.pet_id ?? "";
          }}
        >
          <option value="">All cats (shared)</option>
          {#each pets as p}
            <option value={p.id}>{p.name || p.id}</option>
          {/each}
        </select>
      </label>
    {/each}
  {/if}
</section>
