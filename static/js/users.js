function changePerPage(val) {
    const url = new URL(window.location);
    url.searchParams.set('per_page', val);
    url.searchParams.set('page', '1');
    window.location = url;
}
