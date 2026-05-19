// content.js — extracts relevant job description text from the current page

// ─────────────────────────────────────────────────────────────────────────────
// PLATFORM DETECTION — identify which job site we're on from the URL
// ─────────────────────────────────────────────────────────────────────────────
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
  return null; // unknown platform
}

function extractJobContent() {
  // Priority selectors for major job platforms
  const platformSelectors = [
    // LinkedIn
    '.jobs-description__content',
    '.job-details-jobs-unified-top-card__job-title',
    '.jobs-unified-top-card__job-title',
    '.jobs-description',
    // Wellfound / AngelList
    '.job-description',
    '[data-test="job-description"]',
    '.styles_component__2b0Bd',
    // Naukri
    '.job-desc',
    '.JDC__dang-inner-html',
    '.styles_jhc__desc__WcCLS',
    // Indeed
    '#jobDescriptionText',
    '.jobsearch-jobDescriptionText',
    // Glassdoor
    '.JobDetails_jobDescription__uW_fK',
    '[data-test="description"]',
    // Internshala
    '.internship_details',
    '.about_company',
    // WorkAtAStartup / YC
    '.ycdc-card',
    '.company-description',
    // Generic fallbacks
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[class*="job_description"]',
    '[id*="job-description"]',
    '[id*="jobDescription"]',
    'article',
    'main',
  ];

  let jobText = '';

  for (const selector of platformSelectors) {
    const el = document.querySelector(selector);
    if (el && el.innerText.trim().length > 100) {
      jobText = el.innerText.trim();
      break;
    }
  }

  // Fallback: grab body text, strip nav/header/footer noise
  if (!jobText) {
    const skipTags = ['script', 'style', 'nav', 'header', 'footer', 'aside'];
    const clone = document.body.cloneNode(true);
    skipTags.forEach(tag => {
      clone.querySelectorAll(tag).forEach(el => el.remove());
    });
    jobText = clone.innerText.trim();
  }

  // Trim to ~4000 chars to avoid token overflow
  if (jobText.length > 4000) {
    jobText = jobText.substring(0, 4000) + '\n...[truncated]';
  }

  return {
    text: jobText,
    url: window.location.href,
    title: document.title,
    platform: detectPlatform(),
  };
}

// Listen for message from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extractJob') {
    const result = extractJobContent();
    sendResponse(result);
  }
  return true;
});
