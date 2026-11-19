"use strict";

function decode(text) {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0)).buffer;
}

function encode(buffer) {
  const text = String.fromCharCode(...new Uint8Array(buffer));
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function withDecodedIds(descriptors) {
  return (descriptors || []).map((d) => ({ ...d, id: decode(d.id) }));
}

async function create(options) {
  const key = {
    ...options,
    challenge: decode(options.challenge),
    user: { ...options.user, id: decode(options.user.id) },
    excludeCredentials: withDecodedIds(options.excludeCredentials),
  };
  const credential = await navigator.credentials.create({ publicKey: key });
  return {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: encode(credential.response.clientDataJSON),
      attestationObject: encode(credential.response.attestationObject),
      transports: credential.response.getTransports ? credential.response.getTransports() : [],
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

async function get(options) {
  const key = {
    ...options,
    challenge: decode(options.challenge),
    allowCredentials: withDecodedIds(options.allowCredentials),
  };
  const credential = await navigator.credentials.get({ publicKey: key });
  const response = credential.response;
  return {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: encode(response.clientDataJSON),
      authenticatorData: encode(response.authenticatorData),
      signature: encode(response.signature),
      userHandle: response.userHandle ? encode(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

const STEPS = {
  create: { options: "/account/passkeys/options", run: create },
  get: { options: "/signin/passkey/options", run: get },
};

async function submitWith(form, step) {
  const field = form.querySelector("input[name=credential]");
  const message = form.nextElementSibling;
  try {
    const response = await fetch(step.options, { method: "POST", credentials: "same-origin" });
    if (!response.ok) {
      throw new Error("Could not start the passkey request.");
    }
    field.value = JSON.stringify(await step.run(await response.json()));
    form.submit();
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  }
}

for (const form of document.querySelectorAll("form[data-passkey]")) {
  form.addEventListener("submit", (event) => {
    const field = form.querySelector("input[name=credential]");
    if (field.value) {
      return;
    }
    event.preventDefault();
    submitWith(form, STEPS[form.dataset.passkey]);
  });
}
