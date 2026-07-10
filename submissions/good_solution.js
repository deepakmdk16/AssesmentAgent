const data = require("fs").readFileSync(0, "utf8").split(/\s+/).filter(Boolean);
const n = parseInt(data[0], 10);
const nums = data.slice(1, 1 + n).map(Number);

let best = nums[0];
let current = nums[0];
for (let i = 1; i < n; i++) {
  current = Math.max(nums[i], current + nums[i]);
  best = Math.max(best, current);
}
console.log(best);
