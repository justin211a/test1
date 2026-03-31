/**
 * GFA DATA 수집 - Content Script
 * Scrapes performance data from GFA (Naver Ads) management pages.
 *
 * This script listens for messages from the popup and extracts data
 * from the performance table on the current page.
 */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action !== 'scrapeGFA') return;

  try {
    const result = scrapePerformanceTable();
    sendResponse(result);
  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }

  return true; // Keep message channel open for async response
});

/**
 * Scrape performance data from the GFA management page table.
 */
function scrapePerformanceTable() {
  // Try multiple selectors for different GFA page layouts
  const tableSelectors = [
    'table.report-table',
    'table.data-table',
    'table[class*="performance"]',
    'table[class*="report"]',
    '.report-wrap table',
    '.table-wrap table',
    'table tbody',
  ];

  let table = null;
  for (const selector of tableSelectors) {
    table = document.querySelector(selector);
    if (table) break;
  }

  // Fallback: find any table with performance-like headers
  if (!table) {
    const allTables = document.querySelectorAll('table');
    for (const t of allTables) {
      const headerText = t.querySelector('thead, tr:first-child')?.textContent || '';
      if (headerText.includes('노출') || headerText.includes('클릭') || headerText.includes('비용')) {
        table = t;
        break;
      }
    }
  }

  if (!table) {
    throw new Error('성과 데이터 테이블을 찾을 수 없습니다. GFA 리포트 페이지에서 실행해주세요.');
  }

  // Extract headers
  const headerRow = table.querySelector('thead tr') || table.querySelector('tr:first-child');
  const headers = [];
  headerRow.querySelectorAll('th, td').forEach((cell) => {
    headers.push(cell.textContent.trim());
  });

  // Map Korean headers to standard field names
  const headerMap = {
    '캠페인': 'campaign_name',
    '캠페인명': 'campaign_name',
    '캠페인 ID': 'campaign_id',
    '광고그룹': 'ad_group_name',
    '광고그룹명': 'ad_group_name',
    '광고그룹 ID': 'ad_group_id',
    '노출수': 'impressions',
    '노출': 'impressions',
    '클릭수': 'clicks',
    '클릭': 'clicks',
    '비용': 'cost',
    '총비용': 'cost',
    '광고비': 'cost',
    '전환수': 'conversions',
    '전환': 'conversions',
    '전환매출': 'conversion_value',
    '전환매출액': 'conversion_value',
    '날짜': 'date',
    '일자': 'date',
  };

  const mappedHeaders = headers.map((h) => headerMap[h] || h);

  // Extract data rows
  const rows = [];
  const dataRows = table.querySelectorAll('tbody tr, tr:not(:first-child)');

  dataRows.forEach((tr) => {
    // Skip header rows and summary/total rows
    if (tr.querySelector('th')) return;
    const cells = tr.querySelectorAll('td');
    if (cells.length === 0) return;

    const rowText = tr.textContent;
    if (rowText.includes('합계') || rowText.includes('총합') || rowText.includes('Total')) return;

    const rowData = {};
    cells.forEach((cell, index) => {
      if (index < mappedHeaders.length) {
        let value = cell.textContent.trim();
        // Remove number formatting (commas, currency symbols)
        if (['impressions', 'clicks', 'cost', 'conversions', 'conversion_value'].includes(mappedHeaders[index])) {
          value = value.replace(/[,원₩\s]/g, '');
          value = parseFloat(value) || 0;
        }
        rowData[mappedHeaders[index]] = value;
      }
    });

    // Only include rows with actual data
    if (rowData.impressions || rowData.clicks || rowData.cost) {
      rows.push(rowData);
    }
  });

  // Try to extract the report date from the page
  let reportDate = null;
  const datePatterns = [
    /(\d{4})[.-](\d{2})[.-](\d{2})/,  // 2026-03-30 or 2026.03.30
  ];
  const dateElements = document.querySelectorAll('.date-range, .report-date, [class*="date"], .period');
  for (const el of dateElements) {
    for (const pattern of datePatterns) {
      const match = el.textContent.match(pattern);
      if (match) {
        reportDate = `${match[1]}-${match[2]}-${match[3]}`;
        break;
      }
    }
    if (reportDate) break;
  }

  // Fallback: use yesterday's date
  if (!reportDate) {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    reportDate = yesterday.toISOString().split('T')[0];
  }

  return {
    success: true,
    rows: rows,
    date: reportDate,
    headers: headers,
    rowCount: rows.length,
  };
}
