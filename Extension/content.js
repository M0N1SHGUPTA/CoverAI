// content.js — injected into the job page to scrape the job description
//
// The popup can't read another tab's DOM directly (Chrome sandboxes them),
// so this script acts as the bridge. It gets injected into the active tab,
// reads the page, and sends the data back via Chrome's message passing.

// Figure out which job site we're on based on the URL
function detectPlatform() {
  const host = window.location.hostname.toLowerCase();
  if (host.includes('linkedin.com'))    return 'LinkedIn';
  if (host.includes('naukri.com'))      return 'Naukri';
  if (host.includes('indeed.com'))      return 'Indeed';
  if (host.includes('glassdoor.com') || host.includes('glassdoor.co')) return 'Glassdoor';
  if (host.includes('internshala.com')) return 'Internshala';
  if (host.includes('wellfound.com') || host.includes('angel.co')) return 'Wellfound';
  if (host.includes('workatastartup.com') || host.includes('ycombinator.com')) return 'YC Jobs';
  if (host.includes('lever.co'))        return 'Lever';
  if (host.includes('greenhouse.io'))   return 'Greenhouse';
  if (host.includes('workday.com'))     return 'Workday';
  if (host.includes('monster.com'))     return 'Monster';
  if (host.includes('ziprecruiter.com')) return 'ZipRecruiter';
  return null;
}

// Try platform-specific selectors first, then fall back to generic ones.
// We need at least 100 chars to avoid grabbing tiny nav elements by mistake.
function extractJobContent() {
  const selectors = [
    '.jobs-description__content', '.jobs-description',                   // LinkedIn
    '.job-description', '[data-test="job-description"]',                 // Wellfound
    '.job-desc', '.JDC__dang-inner-html', '.styles_jhc__desc__WcCLS',   // Naukri
    '#jobDescriptionText', '.jobsearch-jobDescriptionText',              // Indeed
    '.JobDetails_jobDescription__uW_fK', '[data-test="description"]',   // Glassdoor
    '.internship_details', '.about_company',                             // Internshala
    '.ycdc-card', '.company-description',                                // YC
    '[class*="job-description"]', '[class*="jobDescription"]',           // generic
    '[id*="job-description"]', '[id*="jobDescription"]',
    'article', 'main',
  ];

  let jobText = '';

  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el && el.innerText.trim().length > 100) {
      jobText = el.innerText.trim();
      break;
    }
  }

  // Last resort: grab the whole page but strip out navigation and noise
  if (!jobText) {
    const clone = document.body.cloneNode(true);
    ['script', 'style', 'nav', 'header', 'footer', 'aside'].forEach(tag => {
      clone.querySelectorAll(tag).forEach(el => el.remove());
    });
    jobText = clone.innerText.trim();
  }

  // Keep it under 4000 chars so we don't blow up the LLM's token limit
  if (jobText.length > 4000) {
    jobText = jobText.substring(0, 4000) + '\n...[truncated]';
  }

  return { text: jobText, url: window.location.href, title: document.title, platform: detectPlatform() };
}

// The popup sends us a message asking to extract the job. We scrape and reply.
// `return true` keeps the message channel open for the async response.
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extractJob') sendResponse(extractJobContent());
  return true;
});
