<script>
  import { fmtLbs, fmtDateTime } from "./api.js";

  // onToggleInvalid(id, invalid), when given, adds a per-weigh-in action to flag
  // a bad reading (or restore one). Flagged rows stay in the feed, struck through.
  let { activities, onToggleInvalid = null } = $props();
  const cols = $derived(onToggleInvalid ? 4 : 3);
</script>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>When</th>
        <th>Event</th>
        <th class="num">Weight</th>
        {#if onToggleInvalid}<th class="num"></th>{/if}
      </tr>
    </thead>
    <tbody>
      {#if !activities.length}
        <tr><td colspan={cols} class="empty">No matching activity</td></tr>
      {:else}
        {#each activities as r}
          <tr class:invalid={r.invalid}>
            <td>{fmtDateTime(r.timestamp)}</td>
            <td>{r.weight_lbs != null ? r.action.replace(/:.*$/, "") : r.action}</td>
            <td class="num">
              {#if r.weight_lbs != null}
                <span class="pill weight" class:invalid={r.invalid}>{fmtLbs(r.weight_lbs)}</span>
              {/if}
            </td>
            {#if onToggleInvalid}
              <td class="num">
                {#if r.weight_lbs != null}
                  <button class="link-btn" onclick={() => onToggleInvalid(r.id, !r.invalid)}>
                    {r.invalid ? "Restore" : "Mark invalid"}
                  </button>
                {/if}
              </td>
            {/if}
          </tr>
        {/each}
      {/if}
    </tbody>
  </table>
</div>
