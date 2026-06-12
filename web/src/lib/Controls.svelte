<script>
  let { pets, petId = $bindable(), range = $bindable() } = $props();
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
</section>
