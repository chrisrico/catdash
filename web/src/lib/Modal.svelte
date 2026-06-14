<script>
  // Minimal centered dialog: a scrim backdrop + a token-styled panel. The parent
  // mounts it with {#if} so it only exists while open. Closes on ESC, a backdrop
  // click, or the Close button; clicks inside the panel don't close it.
  let { title, onClose, children } = $props();

  let closeBtn;
  // Pull focus into the dialog when it opens (keyboard + screen-reader friendly).
  $effect(() => closeBtn?.focus());

  // Freeze the background while the dialog is up (the page scrolls on <body>).
  $effect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => (document.body.style.overflow = prev);
  });

  const backdropClick = (e) => {
    if (e.target === e.currentTarget) onClose();
  };
</script>

<svelte:window onkeydown={(e) => e.key === "Escape" && onClose()} />

<div class="modal-backdrop" role="presentation" onclick={backdropClick}>
  <div class="modal-panel panel" role="dialog" aria-modal="true" aria-label={title}>
    <div class="panel-head">
      <h2>{title}</h2>
      <button bind:this={closeBtn} class="btn secondary sm" aria-label="Close" onclick={onClose}>
        Close
      </button>
    </div>
    {@render children?.()}
  </div>
</div>
