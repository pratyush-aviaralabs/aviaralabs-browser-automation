// Shared loading-state helper for every paginated table across the
// dashboard — shows a spinner + a looping "please wait" message in
// place of a blank table while a fetch is in flight, instead of an
// empty-looking table until data arrives.
const LOADING_MESSAGES = [
  'Loading…',
  'Please wait a moment…',
  'Fetching the latest data…',
  'Almost there…',
];

function showTableLoading(tbody, colSpan) {
  const row = document.createElement('tr');
  row.innerHTML = `
    <td colspan="${colSpan}">
      <div class="sc-loading">
        <div class="sc-spinner"></div>
        <div class="sc-loading-text">${LOADING_MESSAGES[0]}</div>
      </div>
    </td>
  `;
  tbody.innerHTML = '';
  tbody.appendChild(row);

  const textEl = row.querySelector('.sc-loading-text');
  let i = 0;
  const interval = setInterval(() => {
    if (!textEl.isConnected) {
      clearInterval(interval);
      return;
    }
    i = (i + 1) % LOADING_MESSAGES.length;
    textEl.textContent = LOADING_MESSAGES[i];
  }, 1400);

  return () => clearInterval(interval);
}
