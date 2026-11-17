// Runs before first paint so the saved palette never flashes. app.js owns the menu.
(function () {
  var saved = 'auto';
  try { saved = localStorage.getItem('iht-theme') || 'auto'; } catch (e) {}
  document.documentElement.dataset.theme = saved === 'auto'
    ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'ink' : 'paper') : saved;
})();
