const data = require("fs").readFileSync(0, "utf8").split(/\s+/).filter(Boolean);
const n = parseInt(data[0], 10);
const nums = data.slice(1, 1 + n).map(Number);
console.log(`${Math.max(...nums)} ${nums.reduce((a, b) => a + b, 0)}`);
