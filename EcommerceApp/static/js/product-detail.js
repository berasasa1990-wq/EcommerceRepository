document.addEventListener('DOMContentLoaded', () => {
    const mainImage = document.getElementById('mainProductImage');
    const productInfo = document.getElementById('productDetailInfo');
    const defaultImage = productInfo?.dataset.defaultImage ?? '';

    document.querySelectorAll('.product-detail-variation-row[data-image]').forEach((row) => {
        const preview = () => {
            if (mainImage && mainImage.tagName === 'IMG' && row.dataset.image) {
                mainImage.src = row.dataset.image;
            }
        };
        row.addEventListener('mouseenter', preview);
        row.addEventListener('focusin', preview);
    });

    document.getElementById('detailVariations')?.addEventListener('mouseleave', () => {
        if (mainImage && mainImage.tagName === 'IMG' && defaultImage) {
            mainImage.src = defaultImage;
        }
    });

    // Additional images thumbnails: click to swap as current main image (until page refresh)
    const thumbnails = document.querySelectorAll('.product-thumbnail');
    thumbnails.forEach((thumb) => {
        thumb.addEventListener('click', () => {
            if (!mainImage || mainImage.tagName !== 'IMG') return;
            const src = thumb.dataset.src;
            if (src) {
                mainImage.src = src;
                if (thumb.dataset.srcset) {
                    mainImage.srcset = thumb.dataset.srcset;
                } else {
                    mainImage.removeAttribute('srcset');
                }
                if (thumb.dataset.width) mainImage.width = thumb.dataset.width;
                if (thumb.dataset.height) mainImage.height = thumb.dataset.height;
                thumbnails.forEach((t) => t.classList.remove('active'));
                thumb.classList.add('active');
            }
        });
    });

    const backBtn = document.getElementById('productDetailBack');
    if (backBtn && document.referrer && window.history.length > 1) {
        backBtn.addEventListener('click', (event) => {
            event.preventDefault();
            history.back();
        });
    }

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function updateCartBadge(count) {
        const cartBtn = document.querySelector('.cart-btn');
        if (!cartBtn) return;
        let badge = cartBtn.querySelector('.cart-badge');
        if (count > 0) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'cart-badge';
                cartBtn.appendChild(badge);
            }
            badge.textContent = count;
        } else if (badge) {
            badge.remove();
        }
    }

    function showCartToast(message) {
        let toast = document.querySelector('.cart-toast');
        if (!toast) {
            toast = document.createElement('p');
            toast.className = 'cart-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('cart-toast--visible');
        clearTimeout(showCartToast.timer);
        showCartToast.timer = setTimeout(() => {
            toast.classList.remove('cart-toast--visible');
        }, 2200);
    }

    async function submitAddToCartForm(form) {
        const submitBtn = form.querySelector('[type="submit"]');
        const body = new URLSearchParams(new FormData(form));
        body.set('stay', '1');

        if (submitBtn) submitBtn.disabled = true;
        try {
            const response = await fetch(form.action, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: body.toString(),
            });
            const data = await response.json();
            if (!response.ok || !data.ok) {
                throw new Error(data.message || 'Dodavanje u korpu nije uspjelo.');
            }
            updateCartBadge(data.cart_count);
            showCartToast(data.message);
            if (data.upsell_html && typeof window.handleUpsellResponse === 'function') {
                window.handleUpsellResponse(data.upsell_html);
            }
            if (data.meta_add_to_cart && typeof window.trackMetaAddToCart === 'function') {
                window.trackMetaAddToCart(data.meta_add_to_cart);
            }
        } catch (err) {
            showCartToast(err.message || 'Dodavanje u korpu nije uspjelo.');
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    }

    document.querySelectorAll('form.add-to-cart-form, form.product-detail-variation-form').forEach((form) => {
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            submitAddToCartForm(form);
        });
    });

    document.querySelectorAll('.product-qty-selector').forEach((selector) => {
        const input = selector.querySelector('.product-qty-input');
        const minusBtn = selector.querySelector('.product-qty-btn--minus');
        const plusBtn = selector.querySelector('.product-qty-btn--plus');
        if (!input || !minusBtn || !plusBtn) return;

        const min = Number.parseInt(input.min, 10) || 1;
        const max = Number.parseInt(input.max, 10) || 99;

        const syncButtons = () => {
            const value = Number.parseInt(input.value, 10) || min;
            minusBtn.disabled = value <= min;
            plusBtn.disabled = value >= max;
        };

        minusBtn.addEventListener('click', () => {
            const value = Number.parseInt(input.value, 10) || min;
            if (value > min) {
                input.value = String(value - 1);
                syncButtons();
            }
        });

        plusBtn.addEventListener('click', () => {
            const value = Number.parseInt(input.value, 10) || min;
            if (value < max) {
                input.value = String(value + 1);
                syncButtons();
            }
        });

        input.addEventListener('change', () => {
            let value = Number.parseInt(input.value, 10);
            if (!Number.isFinite(value) || value < min) value = min;
            if (value > max) value = max;
            input.value = String(value);
            syncButtons();
        });

        syncButtons();
    });
});