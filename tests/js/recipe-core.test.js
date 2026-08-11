const test = require('node:test');
const assert = require('node:assert');
const C = require('../../public/recipe-core.js');

test('slugify: 中文与符号生成稳定锚点', () => {
  assert.strictEqual(C.slugify('经典肠粉'), 'sec-经典肠粉');
  assert.strictEqual(C.slugify('  酱汁 / 备料  '), 'sec-酱汁-备料');
  assert.strictEqual(C.slugify(''), 'sec-');
});

test('slugify: 去重器返回带序号的唯一锚点', () => {
  const uniq = C.makeUniqueSlugger();
  assert.strictEqual(uniq('经典肠粉'), 'sec-经典肠粉');
  assert.strictEqual(uniq('经典肠粉'), 'sec-经典肠粉-2');
});

test('matchRecipe: 空词命中所有，大小写不敏感', () => {
  assert.strictEqual(C.matchRecipe('鲜虾肠粉 米浆 80g', ''), true);
  assert.strictEqual(C.matchRecipe('Beef Rice Roll', 'beef'), true);
  assert.strictEqual(C.matchRecipe('鲜虾肠粉', '牛肉'), false);
});

test('normalizeTheme: 仅接受合法值否则回退 auto', () => {
  assert.strictEqual(C.normalizeTheme('dark'), 'dark');
  assert.strictEqual(C.normalizeTheme('light'), 'light');
  assert.strictEqual(C.normalizeTheme('auto'), 'auto');
  assert.strictEqual(C.normalizeTheme('weird'), 'auto');
  assert.strictEqual(C.normalizeTheme(null), 'auto');
});

test('clampFontPx: 夹在 12–20 并取整', () => {
  assert.strictEqual(C.clampFontPx(14), 14);
  assert.strictEqual(C.clampFontPx(9), 12);
  assert.strictEqual(C.clampFontPx(99), 20);
  assert.strictEqual(C.clampFontPx('16'), 16);
  assert.strictEqual(C.clampFontPx('x'), 14);
});

test('nextTheme: auto -> light -> dark -> auto 循环', () => {
  assert.strictEqual(C.nextTheme('auto'), 'light');
  assert.strictEqual(C.nextTheme('light'), 'dark');
  assert.strictEqual(C.nextTheme('dark'), 'auto');
  assert.strictEqual(C.nextTheme('weird'), 'light');
});

test('themeLabel: 给出可读标签', () => {
  assert.strictEqual(C.themeLabel('auto'), '跟随系统');
  assert.strictEqual(C.themeLabel('light'), '浅色');
  assert.strictEqual(C.themeLabel('dark'), '深色');
});

test('clampFactor: 夹在 0.1–99，非法回退 1', () => {
  assert.strictEqual(C.clampFactor(2), 2);
  assert.strictEqual(C.clampFactor('1.5'), 1.5);
  assert.strictEqual(C.clampFactor(0), 0.1);
  assert.strictEqual(C.clampFactor(1000), 99);
  assert.strictEqual(C.clampFactor('x'), 1);
});

test('formatQty: 最多2位小数去尾0', () => {
  assert.strictEqual(C.formatQty(2), '2');
  assert.strictEqual(C.formatQty(1.5), '1.5');
  assert.strictEqual(C.formatQty(1.333333), '1.33');
  assert.strictEqual(C.formatQty(2.0), '2');
});

test('scaleText: factor=1 原样返回', () => {
  assert.strictEqual(C.scaleText('米浆 80g', 1), '米浆 80g');
});

test('scaleText: 基础计量单位按倍率缩放', () => {
  assert.strictEqual(C.scaleText('米浆 80g', 2), '米浆 160g');
  assert.strictEqual(C.scaleText('鲜虾 3 只', 2), '鲜虾 6 只');
  assert.strictEqual(C.scaleText('盐 0.5斤', 2), '盐 1斤');
});

test('scaleText: 跳过时间/温度/比例', () => {
  assert.strictEqual(C.scaleText('蒸 90 秒', 2), '蒸 90 秒');
  assert.strictEqual(C.scaleText('油温 180 度', 2), '油温 180 度');
  assert.strictEqual(C.scaleText('腌 10 分钟', 2), '腌 10 分钟');
  assert.strictEqual(C.scaleText('生抽:老抽:糖:水 = 5:1:2:6', 2), '生抽:老抽:糖:水 = 5:1:2:6');
});

test('scaleText: 混合单位 / 区间 / 分数', () => {
  assert.strictEqual(C.scaleText('1斤2两', 2), '2斤4两');
  assert.strictEqual(C.scaleText('3-4只', 2), '6-8只');
  assert.strictEqual(C.scaleText('1/2斤', 2), '1斤');
});

test('scaleText: 适量/空 不动', () => {
  assert.strictEqual(C.scaleText('葱花 适量', 3), '葱花 适量');
  assert.strictEqual(C.scaleText('', 2), '');
  assert.strictEqual(C.scaleText(null, 2), '');
});
