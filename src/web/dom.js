export const $ = (id) => document.getElementById(id);
export const el = (tag, props) => Object.assign(document.createElement(tag), props);
export const setStatus = (text) => ($("status").textContent = text);
