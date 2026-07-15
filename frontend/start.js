const http = require('http');

// Patch outgoing HTTP requests with longer timeout for LLM query proxy
const origRequest = http.request;
http.request = function (...args) {
  const req = origRequest.apply(this, args);
  req.setTimeout(120_000);
  req.on('timeout', () => {
    // Let the error propagate naturally — don't destroy socket prematurely
  });
  return req;
};

require('./server.js');
