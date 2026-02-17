/**
 * ConCall Landing Page — Interactions
 * Handles: nav scroll, FAQ accordion, pricing toggle, mobile menu,
 * scroll animations, smooth scroll
 */

(function () {
    'use strict';

    // =========================================================================
    // Nav scroll effect
    // =========================================================================
    const nav = document.getElementById('mainNav');
    if (nav) {
        const onScroll = () => {
            nav.classList.toggle('scrolled', window.scrollY > 20);
        };
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    }

    // =========================================================================
    // Mobile hamburger menu
    // =========================================================================
    const hamburger = document.getElementById('hamburgerBtn');
    const navLinks = document.getElementById('navLinks');

    if (hamburger && navLinks) {
        hamburger.addEventListener('click', () => {
            navLinks.classList.toggle('open');
            const isOpen = navLinks.classList.contains('open');
            hamburger.setAttribute('aria-expanded', isOpen);
        });

        // Close menu on link click
        navLinks.querySelectorAll('.nav__link').forEach(link => {
            link.addEventListener('click', () => {
                navLinks.classList.remove('open');
            });
        });
    }

    // =========================================================================
    // FAQ Accordion
    // =========================================================================
    document.querySelectorAll('.faq-item__q').forEach(btn => {
        btn.addEventListener('click', () => {
            const item = btn.closest('.faq-item');
            const isOpen = item.classList.contains('open');

            // Close all others
            document.querySelectorAll('.faq-item.open').forEach(openItem => {
                if (openItem !== item) {
                    openItem.classList.remove('open');
                    openItem.querySelector('.faq-item__q').setAttribute('aria-expanded', 'false');
                }
            });

            item.classList.toggle('open', !isOpen);
            btn.setAttribute('aria-expanded', !isOpen);
        });
    });

    // =========================================================================
    // Pricing Toggle (Monthly / Annual)
    // =========================================================================
    const pricingToggle = document.getElementById('pricingToggle');
    const monthlyLabel = document.getElementById('monthlyLabel');
    const annualLabel = document.getElementById('annualLabel');

    if (pricingToggle) {
        pricingToggle.addEventListener('change', () => {
            const isAnnual = pricingToggle.checked;
            monthlyLabel.classList.toggle('active', !isAnnual);
            annualLabel.classList.toggle('active', isAnnual);

            document.querySelectorAll('.price-value').forEach(el => {
                const monthly = el.dataset.monthly;
                const annual = el.dataset.annual;
                el.textContent = isAnnual ? annual : monthly;
            });
        });
    }

    // =========================================================================
    // Scroll-triggered fade-in animations
    // =========================================================================
    if ('IntersectionObserver' in window) {
        const fadeObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    fadeObserver.unobserve(entry.target);
                }
            });
        }, {
            threshold: 0.1,
            rootMargin: '0px 0px -40px 0px'
        });

        document.querySelectorAll('.fade-up').forEach(el => {
            fadeObserver.observe(el);
        });
    } else {
        // Fallback: show everything
        document.querySelectorAll('.fade-up').forEach(el => {
            el.classList.add('visible');
        });
    }

    // =========================================================================
    // File tree tab clicks (in embedded app)
    // =========================================================================
    document.querySelectorAll('.filetree__item[data-tab]').forEach(item => {
        item.addEventListener('click', () => {
            if (typeof switchTab === 'function') {
                switchTab(item.dataset.tab);
            }
        });
    });

})();
