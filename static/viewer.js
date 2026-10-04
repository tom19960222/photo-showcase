const viewer = document.querySelector('.viewer');
const entries = [...document.querySelectorAll('.photo-list .photo-link')];
const stage = viewer.querySelector('.viewer-stage');
const strip = viewer.querySelector('.viewer-strip');
const previous = viewer.querySelector('.viewer-previous');
const next = viewer.querySelector('.viewer-next');
const albumPath = entries[0].pathname.slice(0, entries[0].pathname.lastIndexOf('/'));
const albumTitle = document.querySelector('.album-heading h1').textContent;
let current = -1;
let opener = null;

function showPhoto(index) {
  current = index;
  if (!strip.children.length) {
    entries.forEach((entry, position) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('aria-label', `第 ${position + 1} 張：${entry.closest('figure').querySelector('figcaption').textContent}`);
      button.append(entry.querySelector('img').cloneNode());
      button.addEventListener('click', () => changePhoto(position));
      strip.append(button);
    });
  }
  const entry = entries[index];
  const frame = entry.closest('.photo-frame').cloneNode(true);
  const image = frame.querySelector('img');
  const filename = entry.closest('figure').querySelector('figcaption').textContent;
  image.loading = 'eager';
  image.alt = filename;
  frame.querySelector('a').replaceWith(image);
  const oldFrame = stage.querySelector('.photo-frame');
  if (oldFrame?.contains(document.activeElement)) viewer.querySelector('.viewer-close').focus();
  oldFrame?.remove();
  stage.prepend(frame);
  watchPhoto(frame, viewer.querySelector('.viewer-close'));
  viewer.querySelector('.viewer-filename').textContent = filename;
  viewer.querySelector('.viewer-count').textContent = `${index + 1} / ${entries.length}`;
  document.title = `${filename} · ${albumTitle}`;
  previous.disabled = index === 0;
  next.disabled = index === entries.length - 1;
  [...strip.children].forEach((button, position) => button.setAttribute('aria-current', String(position === index)));
  if (!viewer.open) {
    viewer.showModal();
    document.body.classList.add('viewer-open');
  }
  strip.children[index].scrollIntoView({block: 'nearest', inline: 'nearest'});
}

function changePhoto(index) {
  if (index < 0 || index >= entries.length || index === current) return;
  history.replaceState(history.state, '', entries[index].href);
  showPhoto(index);
}

function hideViewer() {
  viewer.close();
  document.body.classList.remove('viewer-open');
  document.title = albumTitle;
  (opener || entries[current])?.focus();
}

function closeViewer() {
  if (history.state?.viewerEntry) {
    history.back();
  } else {
    history.replaceState(null, '', albumPath);
    hideViewer();
  }
}

for (const entry of entries) {
  entry.addEventListener('click', (event) => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    opener = entry;
    history.pushState({viewerEntry: true, opener: entry.pathname}, '', entry.href);
    showPhoto(entries.indexOf(entry));
  });
}
viewer.querySelector('.viewer-close').addEventListener('click', closeViewer);
previous.addEventListener('click', () => changePhoto(current - 1));
next.addEventListener('click', () => changePhoto(current + 1));
viewer.addEventListener('cancel', (event) => {
  event.preventDefault();
  closeViewer();
});
viewer.addEventListener('keydown', (event) => {
  if (event.target.matches('input, textarea, select, [contenteditable]')) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault();
    changePhoto(current + (event.key === 'ArrowRight' ? 1 : -1));
  }
  if (event.key !== 'Tab') return;
  const controls = [...viewer.querySelectorAll('button:not(:disabled), a[href]')].filter(control => control.getClientRects().length);
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  }
});
let touchStart = null;
stage.addEventListener('touchstart', (event) => {
  touchStart = event.touches.length === 1 ? event.touches[0] : null;
}, {passive: true});
stage.addEventListener('touchend', (event) => {
  if (!touchStart) return;
  const dx = event.changedTouches[0].clientX - touchStart.clientX;
  const dy = event.changedTouches[0].clientY - touchStart.clientY;
  touchStart = null;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) changePhoto(current + (dx < 0 ? 1 : -1));
}, {passive: true});
stage.addEventListener('touchcancel', () => { touchStart = null; }, {passive: true});
window.addEventListener('popstate', () => {
  const index = entries.findIndex(entry => entry.pathname === location.pathname);
  if (index < 0) hideViewer();
  else showPhoto(index);
});

const selected = document.querySelector('.album-page').dataset.selected;
const initial = entries.findIndex(entry => entry.closest('figure').querySelector('figcaption').textContent === selected);
if (initial >= 0) {
  opener = entries.find(entry => entry.pathname === history.state?.opener) || entries[initial];
  showPhoto(initial);
}
