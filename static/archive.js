const archiveTools = document.querySelector('.album-tools');
if (archiveTools) {
  const start = archiveTools.querySelector('.prepare-archive');
  const cancel = archiveTools.querySelector('.cancel-archive');
  const status = archiveTools.querySelector('.archive-status');
  const progress = archiveTools.querySelector('progress');
  const download = archiveTools.querySelector('.download-archive');
  let objectURL;
  let operation;

  const finish = (message) => {
    start.disabled = false;
    cancel.hidden = true;
    progress.hidden = true;
    status.textContent = message;
  };

  cancel.addEventListener('click', () => {
    operation?.abort();
    operation = null;
    finish('已取消準備');
    start.focus();
  });

  start.addEventListener('click', async () => {
    URL.revokeObjectURL(objectURL);
    download.hidden = true;
    start.disabled = true;
    cancel.hidden = false;
    progress.hidden = false;
    status.textContent = '正在準備相簿 ZIP…';
    const current = new AbortController();
    operation = current;
    try {
      const response = await fetch(archiveTools.dataset.archiveUrl, { signal: current.signal });
      if (!response.ok || response.headers.get('Content-Type') !== 'application/zip') throw new Error('ZIP');
      // ponytail: 完整 ZIP 先交給瀏覽器 Blob；相簿超過裝置容量時再改成伺服器暫存下載。
      const content = await response.blob();
      if (operation !== current) return;
      objectURL = URL.createObjectURL(content);
      download.href = objectURL;
      download.hidden = false;
      finish('ZIP 已準備完成');
    } catch {
      if (operation === current) finish('相簿 ZIP 準備失敗，請重試');
    }
  });
}
