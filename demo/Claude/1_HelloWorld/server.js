const express = require("express");
const app = express();
const PORT = 3000;

app.get("/", (_req, res) => {
  res.send("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Hello</title></head><body><h1>Hello, World</h1></body></html>");
});

app.listen(PORT, () => {
  console.log(`Server running at http://localhost:${PORT}`);
});
