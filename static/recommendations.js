document.querySelectorAll('[data-recommendation-form]').forEach((form) => {
    const button = form.querySelector('[data-recommendation-submit]');
    const loading = form.querySelector('[data-recommendation-loading]');
    const preferences = form.querySelector('textarea');
    let submitting = false;
    const label = button.innerHTML;
    const initiallyDisabled = button.disabled;
    form.addEventListener('submit', (event) => {
        if (submitting || initiallyDisabled) {
            event.preventDefault();
            return;
        }
        submitting = true;
        button.disabled = true;
        button.textContent = 'Choosing your concerts…';
        preferences.readOnly = true;
        loading.hidden = false;
        form.setAttribute('aria-busy', 'true');
    });
    window.addEventListener('pageshow', (event) => {
        if (event.persisted && submitting) {
            // A consumed form restored by Back needs a fresh token before reuse.
            window.location.reload();
            return;
        }
        submitting = false;
        button.disabled = initiallyDisabled;
        button.innerHTML = label;
        preferences.readOnly = false;
        loading.hidden = true;
        form.removeAttribute('aria-busy');
    });
});
const feedback = document.querySelector('[data-recommendation-feedback]');
if (feedback) feedback.focus();
