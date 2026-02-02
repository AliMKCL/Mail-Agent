// Split Screen Elements
const mainContainer = document.getElementById('mainContainer');
const getStartedBtn = document.getElementById('getStartedBtn');

// Auth Form Elements
const signInTab = document.getElementById('signInTab');
const signUpTab = document.getElementById('signUpTab');
const authForm = document.getElementById('authForm');
const submitBtn = document.getElementById('submitBtn');
const togglePassword = document.getElementById('togglePassword');
const passwordInput = document.getElementById('password');

// State
let isSignIn = true;
let isSplitView = false;

// Trigger split-screen animation with staged grid transition
getStartedBtn.addEventListener('click', () => {
    if (!isSplitView) {
        // Stage 1: Start split view (boxes compress in 4x1 grid)
        mainContainer.classList.add('split-view');

        // Stage 2: Immediately start fading out (0.5s duration)
        setTimeout(() => {
            mainContainer.classList.add('grid-transition');
        }, 0);

        // Stage 3: After 500ms (0.5s fade out), change to 2x2 and fade in (0.5s)
        setTimeout(() => {
            mainContainer.classList.remove('grid-transition');
            mainContainer.classList.add('grid-changed');
        }, 500);

        isSplitView = true;
    }
});

// Tab switching
signInTab.addEventListener('click', () => {
    if (!isSignIn) {
        isSignIn = true;
        signInTab.classList.add('active');
        signUpTab.classList.remove('active');
        submitBtn.textContent = 'Sign In';
    }
});

signUpTab.addEventListener('click', () => {
    if (isSignIn) {
        isSignIn = false;
        signUpTab.classList.add('active');
        signInTab.classList.remove('active');
        submitBtn.textContent = 'Sign Up';
    }
});

// Toggle password visibility
togglePassword.addEventListener('click', () => {
    const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
    passwordInput.setAttribute('type', type);

    // Update icon
    const icon = togglePassword.querySelector('svg path');
    if (type === 'text') {
        // Eye slash icon
        icon.setAttribute('d', 'M12 7c2.76 0 5 2.24 5 5 0 .65-.13 1.26-.36 1.83l2.92 2.92c1.51-1.26 2.7-2.89 3.43-4.75-1.73-4.39-6-7.5-11-7.5-1.4 0-2.74.25-3.98.7l2.16 2.16C10.74 7.13 11.35 7 12 7zM2 4.27l2.28 2.28.46.46C3.08 8.3 1.78 10.02 1 12c1.73 4.39 6 7.5 11 7.5 1.55 0 3.03-.3 4.38-.84l.42.42L19.73 22 21 20.73 3.27 3 2 4.27zM7.53 9.8l1.55 1.55c-.05.21-.08.43-.08.65 0 1.66 1.34 3 3 3 .22 0 .44-.03.65-.08l1.55 1.55c-.67.33-1.41.53-2.2.53-2.76 0-5-2.24-5-5 0-.79.2-1.53.53-2.2zm4.31-.78l3.15 3.15.02-.16c0-1.66-1.34-3-3-3l-.17.01z');
    } else {
        // Eye icon
        icon.setAttribute('d', 'M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z');
    }
});

// Form submission
authForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;

    // Disable submit button during request
    submitBtn.disabled = true;
    submitBtn.textContent = isSignIn ? 'Signing in...' : 'Signing up...';

    try {
        if (isSignIn) {
            // Sign-in API call
            console.log('Attempting sign-in with:', email);
            const response = await fetch('http://localhost:8000/api/auth/signin', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    email: email,
                    password: password
                })
            });

            console.log('Response status:', response.status);
            console.log('Response ok:', response.ok);

            const data = await response.json();
            console.log('Response data:', data);

            if (response.ok) {
                console.log('Sign-in successful:', data);
                // Store token if your backend returns one
                if (data.token) {
                    localStorage.setItem('authToken', data.token);
                }
                // Redirect to main app
                console.log('Redirecting to templates/main.html');
                window.location.href = 'templates/main.html';
            } else {
                console.error('Sign-in failed:', data.message);
                alert(data.message || 'Sign-in failed. Please try again.');
            }
        } else {
            // Sign-up API call
            const response = await fetch('http://localhost:8000/api/auth/signup', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    email: email,
                    password: password
                })
            });

            const data = await response.json();

            if (response.ok) {
                console.log('Sign-up successful:', data);
                // Store email_account_id for use in the main app
                if (data.email_account_id) {
                    localStorage.setItem('email_account_id', data.email_account_id);
                }
                // Store account_id as well
                if (data.account_id) {
                    localStorage.setItem('account_id', data.account_id);
                }
                // Store token if your backend returns one
                if (data.token) {
                    localStorage.setItem('authToken', data.token);
                }
                // Redirect to main app
                window.location.href = 'templates/main.html';
            } else {
                console.error('Sign-up failed:', data.message);
                alert(data.message || 'Sign-up failed. Please try again.');
            }
        }
    } catch (error) {
        console.error('Authentication error:', error);
        alert('An error occurred. Please try again.');
    } finally {
        // Re-enable submit button
        submitBtn.disabled = false;
        submitBtn.textContent = isSignIn ? 'Sign In' : 'Sign Up';
    }
});

// OAuth button handlers
const googleBtn = document.querySelector('.google-btn');
const microsoftBtn = document.querySelector('.microsoft-btn');

googleBtn.addEventListener('click', () => {
    console.log('Google OAuth clicked');
    // Add Google OAuth logic here
    // For now, redirect to the main app
    window.location.href = 'templates/index.html';
});

microsoftBtn.addEventListener('click', () => {
    console.log('Microsoft OAuth clicked');
    // Add Microsoft OAuth logic here
    // For now, redirect to the main app
    window.location.href = 'templates/index.html';
});

// Add smooth scroll behavior
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// Add animation on scroll for feature cards
const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
        }
    });
}, observerOptions);

document.querySelectorAll('.feature-card').forEach(card => {
    card.style.opacity = '0';
    card.style.transform = 'translateY(20px)';
    card.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    observer.observe(card);
});
