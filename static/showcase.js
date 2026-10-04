for (const frame of document.querySelectorAll('.photo-frame')) {
  const image = frame.querySelector('img');
  const failure = frame.querySelector('.image-error');
  const update = (state) => {
    frame.dataset.state = state;
    frame.setAttribute('aria-busy', String(state === 'loading'));
    failure.hidden = state !== 'error';
  };
  image.addEventListener('load', () => update('ready'));
  image.addEventListener('error', () => update('error'));
  failure.querySelector('button').addEventListener('click', () => {
    update('loading');
    image.src = image.src;
  });
  update(image.complete ? (image.naturalWidth ? 'ready' : 'error') : 'loading');
}
