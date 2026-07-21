const resetApiUrl = window.APP_CONFIG?.API_BASE_URL || "http://127.0.0.1:8080";
const resetForm = document.getElementById("resetPasswordForm");
const resetStatus = document.getElementById("resetStatus");
const resetToken = new URLSearchParams(window.location.search).get("token");
const newPasswordInput = document.getElementById("newPassword");
const confirmPasswordInput = document.getElementById("confirmPassword");
const resetButton = document.getElementById("resetPasswordButton");
const resetFeedback = document.getElementById("resetPasswordFeedback");
const confirmMessage = document.getElementById("confirmPasswordMessage");

const resetRevealTimers = new Map();
const RESET_REVEAL_MAX_MS = 3000;

function hideResetPassword(toggle) {
  const input = document.getElementById(toggle.dataset.resetPassword);
  if (!input) return;
  window.clearTimeout(resetRevealTimers.get(toggle));
  resetRevealTimers.delete(toggle);
  input.type = "password";
  toggle.classList.remove("is-visible", "is-revealing");
  toggle.setAttribute("aria-label", "Press and hold to reveal password");
}

function hideAllResetPasswords() {
  document.querySelectorAll("[data-reset-password]").forEach(hideResetPassword);
}

function revealResetPassword(toggle) {
  const input = document.getElementById(toggle.dataset.resetPassword);
  if (!input || input.type === "text") return;
  input.type = "text";
  toggle.classList.add("is-visible", "is-revealing");
  toggle.setAttribute("aria-label", "Release to hide password");
  window.clearTimeout(resetRevealTimers.get(toggle));
  resetRevealTimers.set(toggle, window.setTimeout(() => hideResetPassword(toggle), RESET_REVEAL_MAX_MS));
}

function resetRevealButton(event) {
  return event.target.closest?.("[data-reset-password]") || null;
}

["pointerdown", "keydown"].forEach((eventName) => {
  document.addEventListener(eventName, (event) => {
    const toggle = resetRevealButton(event);
    if (!toggle) return;
    if (eventName === "keydown" && ![" ", "Enter"].includes(event.key)) return;
    event.preventDefault();
    revealResetPassword(toggle);
  });
});
["pointerup", "pointercancel", "mouseup", "touchend", "touchcancel", "keyup", "focusout"].forEach((eventName) => {
  document.addEventListener(eventName, (event) => {
    const toggle = resetRevealButton(event);
    if (toggle) hideResetPassword(toggle);
  }, true);
});
document.addEventListener("click", (event) => {
  const toggle = resetRevealButton(event);
  if (!toggle) return;
  event.preventDefault();
  hideResetPassword(toggle);
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) hideAllResetPasswords();
});
window.addEventListener("blur", hideAllResetPasswords);

const resetPasswordRules = [
  { label: "Minimum 8 characters", test: (value) => value.length >= 8 },
  { label: "One uppercase letter", test: (value) => /[A-Z]/.test(value) },
  { label: "One lowercase letter", test: (value) => /[a-z]/.test(value) },
  { label: "One number", test: (value) => /\d/.test(value) },
  { label: "One special character", test: (value) => /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]/.test(value) }
];

function resetPasswordScore(password) {
  return resetPasswordRules.reduce((score, rule) => score + (rule.test(password) ? 1 : 0), 0);
}

function resetPasswordIsStrong(password) {
  return resetPasswordScore(password) === resetPasswordRules.length;
}

function resetStrengthDetails(password) {
  const score = resetPasswordScore(password);
  if (score <= 2) return { label: "Weak", className: "weak", blocks: 2 };
  if (score <= 4) return { label: "Medium", className: "medium", blocks: 4 };
  return { label: "Strong", className: "strong", blocks: 5 };
}

function renderResetPasswordFeedback() {
  const password = newPasswordInput.value;
  if (!password) {
    resetFeedback.innerHTML = "";
    resetFeedback.hidden = true;
    return;
  }

  const failedRules = resetPasswordRules.filter((rule) => !rule.test(password));
  const strength = resetStrengthDetails(password);
  const blocks = Array.from({ length: 5 }, (_, index) =>
    `<span class="${index < strength.blocks ? "filled" : ""}"></span>`
  ).join("");

  resetFeedback.innerHTML = `
    ${failedRules.length ? `<ul class="password-rules">${failedRules.map((rule) => `<li><span>&times;</span>${rule.label}</li>`).join("")}</ul>` : '<p class="password-complete"><span>&#10003;</span>Password meets all requirements</p>'}
    <div class="password-strength ${strength.className}">
      <span>Password Strength:</span>
      <strong>${strength.label}</strong>
      <div class="strength-blocks">${blocks}</div>
      <small>${resetPasswordScore(password)}/${resetPasswordRules.length}</small>
    </div>
  `;
  resetFeedback.hidden = false;
}

function validateResetForm() {
  const password = newPasswordInput.value;
  const confirm = confirmPasswordInput.value;
  const validPassword = resetPasswordIsStrong(password);
  const matches = Boolean(confirm) && password === confirm;

  if (confirm && !matches) {
    confirmMessage.textContent = "Passwords do not match.";
    confirmMessage.hidden = false;
  } else {
    confirmMessage.hidden = true;
  }

  resetButton.disabled = !resetToken || !validPassword || !matches;
}

function showResetStatus(message, isError = true) {
  resetStatus.textContent = message;
  resetStatus.classList.toggle("success", !isError);
  resetStatus.hidden = false;
}

async function readApiResponse(response, emptyMessage) {
  const text = await response.text();
  console.log("Reset password API status:", response.status);
  console.log("Reset password API response text:", text);

  if (!text.trim()) throw new Error(emptyMessage);

  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("Backend returned an invalid response.");
  }
}

if (!resetToken) {
  showResetStatus("Reset link is invalid or expired.");
  resetButton.disabled = true;
}

newPasswordInput.addEventListener("input", () => {
  renderResetPasswordFeedback();
  validateResetForm();
});
confirmPasswordInput.addEventListener("input", validateResetForm);
validateResetForm();

resetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (resetButton.disabled) return;

  const apiUrl = `${resetApiUrl}/auth/reset-password`;
  console.log("Reset password API URL:", apiUrl);

  try {
    const response = await fetch(apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: resetToken,
        new_password: newPasswordInput.value
      })
    });
    const data = await readApiResponse(response, "Backend returned an empty response.");
    if (!response.ok) {
      throw new Error(
        response.status === 400
          ? "Reset link is invalid or expired."
          : data.detail || "Unable to reset password. Please try again."
      );
    }
    showResetStatus("Password reset successful. Please sign in with your new password.", false);
    resetForm.reset();
    resetFeedback.hidden = true;
    confirmMessage.hidden = true;
    resetButton.disabled = true;
  } catch (error) {
    showResetStatus(
      error instanceof TypeError
        ? "Please start the backend server and try again."
        : error.message
    );
  }
});

