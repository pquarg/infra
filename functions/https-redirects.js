// Uvicorn sees HTTP behind the private ALB. Upgrade only redirects whose
// authority exactly matches the viewer host; leave external/relative URLs alone.
function handler(event) {
    var response = event.response;
    var location = response.headers.location;
    var host = event.request.headers.host;
    var redirects = [301, 302, 303, 307, 308];

    if (redirects.indexOf(response.statusCode) !== -1 && location && host) {
        var match = /^http:\/\/([^\/?#]+)([\/?#].*)?$/i.exec(location.value);
        if (match && match[1].toLowerCase() === host.value.toLowerCase()) {
            location.value = 'https://' + host.value + (match[2] || '/');
        }
    }
    return response;
}
