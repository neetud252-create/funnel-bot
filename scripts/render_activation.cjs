// Render the code-native activation banner with Playwright/Chrome.
const {chromium} = require('playwright');
const {resolve} = require('node:path');
const {pathToFileURL} = require('node:url');
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1200,height:600}, deviceScaleFactor:1});
    await page.goto(pathToFileURL(resolve(__dirname,'../assets/activate_account.svg')).href);
    await page.locator('svg').screenshot({path:resolve(__dirname,'../assets/activate_account.png')});
    console.log('Rendered activation banner: 1200 x 600');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
