<script>
  import { fmtLbs, fmtDateTime } from "./api.js";

  let { activities } = $props();
</script>

<div class="table-wrap">
  <table>
    <thead>
      <tr><th>When</th><th>Event</th><th class="num">Weight</th></tr>
    </thead>
    <tbody>
      {#if !activities.length}
        <tr><td colspan="3" class="empty">No activity yet</td></tr>
      {:else}
        {#each activities as r}
          <tr>
            <td>{fmtDateTime(r.timestamp)}</td>
            <td>{r.weight_lbs != null ? r.action.replace(/:.*$/, "") : r.action}</td>
            <td class="num">
              {#if r.weight_lbs != null}
                <span class="pill weight">{fmtLbs(r.weight_lbs)}</span>
              {/if}
            </td>
          </tr>
        {/each}
      {/if}
    </tbody>
  </table>
</div>
