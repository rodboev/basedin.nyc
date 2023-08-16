/* Client examples begin below. (Search "client") POCs and code assesments start here. */

/*
Given the search content below, build a search algorithm that returns the shortest snippet of content that contains all three words in the query in any order.
Example output: "bridge is a landmark. City"
*/

'use strict';

var content = 'The George Washington Bridge in New York City is one of the oldest bridges ever constructed. It is now being remodeled because the bridge is a landmark. City officials say that the landmark bridge effort will create a lot of new jobs in the city.',
    query = 'Landmark City Bridge',
    matches = [],
    distances = [],
    shortest = [],
    ordered = [],
    start,
    end;

// Return multiple matches
function find(haystack, needle) {
    var location = [];
    for(var i = haystack.indexOf(needle); i !== -1; i = haystack.indexOf(needle, i + 1)) {
        location.push(i);
    }
    return location;
}

// Group positions of matches
var contentLC = content.toLowerCase();
query = query.toLowerCase().split(' ');
for (var i = 0; i < query.length; i++)
    matches = matches.concat(find(contentLC, query[i]));
matches.sort(function(a, b) { return a - b; });

// Find shortest query distance
for (var i = 0; i < (matches.length - query.length - 1); i++)
    distances.push(matches[i+query.length-1] - matches[i]);
var shortest = distances.slice();
shortest.sort(function(a, b) { return a - b; });
shortest = shortest[0];

// Build the result
start = matches[distances.indexOf(shortest)];
end = content.indexOf(' ', content.indexOf(parseInt(start) + parseInt(shortest)))+1;
window.console && console.log(
    content.substr(start, shortest) +
    content.substr(parseInt(start)+parseInt(shortest), end)
);




/*
Point of Sale Example

Items have prices per unit but also volume prices. Apples may be $1.00 each or 4 for $3.00. This point-of-sale scanning API accepts an arbitrary ordering of products and returns the correct total price for an entire shopping cart based on the per unit prices or the volume prices as applicable:

Item    Price
A       $2 each or 4 for $7
B       $12 
C       $1.25 each or $6 for a six-pack 
D       $.15 
*/

'use strict';
var terminal = {
    cart: [],
    price: {},
    sale: {},
    total: 0,
    
    setPricing: function() {
        this.price['A'] = 2;
        this.price['B'] = 12;
        this.price['C'] = 1.25;
        this.price['D'] = 0.15;
        
        // TODO make items object with price, qty, sale properties
        this.sale['A'] = [4, 7],
        this.sale['C'] = [6, 6]
        return false;
    },
    
    scan: function(item) {
        
        if (typeof item !== 'undefined') {
            this.cart.push(item);
            this.total += this.price[item];
            // https://stackoverflow.com/questions/5667888/counting-occurences-of-javascript-array-elements
            var counts = {};
            for (var i = 0; i < this.cart.length; i++) {
                var j = this.cart[i];
                counts[j] = counts[j] ? counts[j] + 1 : 1;
            }
            window.console && console.log('Scanned ' + item + ' (' + counts[item] + 'x now)');
            if (typeof this.sale[item] !== 'undefined' &&
                counts[item] % this.sale[item][0] === 0) {
                this.total = this.total - (this.price[item] * this.sale[item][0]) + this.sale[item][1];
            }
            
            return false;
        }
        return true;
    }
};




/*
Client: Amgen Science
Live site: http://www.amgenscience.com/
Description: Swap meta tags on Ajax navigation. Used by ShareThis widget to populate social sharing info.
*/

swapMeta: function(page_title, og_title, og_description, og_image, og_url, meta_description, meta_keywords) {
    document.title = page_title;
    $('#share_nav, #mobile_share').children('span').empty();

    var services = ['facebook', 'twitter', 'linkedin', 'googleplus', 'email'];
    $.each(services, function(index, service) {

        // truncate googleplus to google for ST
        var element = (service === 'googleplus') ?
                'st_google' : 'st_' + service;

        // build protocol and hostname for url and image. encode hash for facebook and twitter
        var root = (window.location.hostname == '127.0.0.1' || window.location.hostname == 'localhost') ?
                'http://amgenscience.dev.rfistudios.com' : location.protocol + '//' + location.hostname;
        var url = (service === 'facebook' || service === 'twitter') ?
                root + og_url.replace('#', '%23') : root + og_url,
            image = root + og_image;

        // this catches an exception for a legal copy requirement. replaces the title passed to twitter for one instance
        var title = ( service === 'twitter' && og_description == 'Kári Stefánsson on how rare variants are used to discover new ways to treat diseases.' ) ?
        og_description : og_title;

        stWidget.addEntry({
            'service': service,
            'element': document.getElementById(element),
            'url': url,
            'title': title,
            'type': 'large',
            'image': image,
            'summary': og_description
        });

        stWidget.addEntry({
            'service': service,
            'element': document.getElementById(element + '_mobile'),
            'url': url,
            'title': title,
            'type': 'large',
            'image': image,
            'summary': og_description
        });
    });
}




/*
Client: Amgen Science
Live site: http://www.amgenscience.com/
Description: Short function that catches all hash changes (Ajax deep linking was implemented without using the HTML5 History API on this site) across the site. Upon implementation, the WebTrends Technical Account Manager was eager to roll out this solution across all sites using Ajax in this way.
*/

trackHashChange: function() {
    var hash = WTActions.toTitleCase(window.location.hash.slice(1).split("-").join(' '));
    WTActions.track({
        argsa: [
            "DCS.dcsuri",
            '#' + hash,
            "WT.ti",
            hash.replace("/", "|"),
            "WT.dl",
            "0",
            "WT.cg_n",
            hash.split('/')[0],
            "WT.cg_s",
            hash.split('/').slice(1).join('/')
        ]
    });
}




/*
Client: Barclays
Product: Barclaycard Arrival

Sampled:
- anonymous containers create animation queue
- pushState deep linking degrades to onhashchange
- hash enhances to URL before $()

Live site: http://www.barclaycardarrival.com/
*/

var card, hashUp,
    hash = window.location.hash.substr(1);

(hashUp = function() {
    if (hash != '' && hash != 'modal') {
        var path = window.location.pathname;
        var parts = path.split('/')
        if (parts[parts.length - 2] == card)
            destination = parts.slice(0, parts.length - 1).join('/') + parts.slice(0, parts.length - 2).join('/') + '/' + hash + '/';
        else if
            (parts[1] == card) destination = parts.slice(0, parts.length - 2).join('/') + '/' + hash + '/';
        else
            destination = parts.slice(parts.length - 1, parts.length - 2).join('/') + '/' + hash + '/';
        window.location = destination;
    }
})();

var $tab = $('.tab');
$tab.on('click', function(e) {
    e.preventDefault();
    var path = $(this)[0].pathname.split('/');
    var slug = path[path.length - 2].replace(/\//g, '');
    var destination = slug.split('-')[0];

    if (location != destination) {
        var $newAll = $current.after($('<div />')).siblings('div');
        var $new = $newAll.first();
        var $newOther = $newAll.not($new);

        if ($tab.filter('.' + location).index() > $tab.filter('.' + destination).index()) {
            var moveNewTo = -(contentOuterWidth + currentPos + caretWidth);
            var moveCurrentTo = contentOuterWidth;
        }
        else {
            var moveNewTo = contentOuterWidth;
            var moveCurrentTo = -(contentOuterWidth + currentPos + caretWidth);
        }

        $new.load($(this).attr('href') + '?ajax')
            .css({
                'left': moveNewTo,
                'position': 'absolute',
                'width': contentWidth,
                'opacity': '0',
                'float': 'left'
            })
            .animate({
                'left': currentPos,
                'opacity': '1'
            }, {
                duration: pageSpeed,
                easing: pageEasing,
                complete: function() {
                    $(this).removeAttr('style');
                }
            })
        $current.add($newOther)
            .css({
                'float': 'left'
            })
            .stop()
            .animate({
                'left': moveCurrentTo,
                'opacity': '0'
            }, {
                duration: pageSpeed,
                easing: pageEasing,
                complete: function() {
                    $content.removeAttr('style');
                    $current.html($new.html())
                        .removeAttr('style');
                    $newAll.remove();
                }
            });
        if (history.pushState) {
            if (e.originalEvent != undefined &&
                    (e.originalEvent.type == 'click' || e.originalEvent.type == 'touchstart')) {
                history.pushState(null, null, $(this).attr('href'));
            }
        } else {
            window.location.hash = slug;
        }
    }

    if (history.pushState) {
        window.onpopstate = function(e) {
            var path = e.target.location.pathname;
            var parts = path.split('/')
            var destination = parts[parts.length - 2] != card ? parts[parts.length - 2].split('-')[0] : '';
            loadPage(destination);
        }
        window.onhashchange = function(e) {
            if (window.location.hash.substr(1) == 'faq') openModal();
            else clearHash();
        }
    } else {
        window.onhashchange = function(e) {
            var destination = window.location.hash.substr(1).split('-')[0];
            if (destination == 'modal') openModal();
            else loadPage(destination);
        }
    }
}



/*
Client: VISA
Product: Swipe it to Win

Sampled:
- coordinates recalculated on orientation change
- fit animation into 338k at 5350x944 for iOS (5050400 used/5242880 available)
- HQ swaps in at interval; no libraries

Archive: http://www.cosgrovenyextranet.com/9997/site/
*/

var cols = 8,
	rows = 4,
	frames = 61,
	xpos = 0,
	ypos = 0,
	index = 0,
	money,
	card,
	width,
	height,
	across,
	loop;

function init() {
    money = document.getElementById('money');
    width = money.clientWidth;
    height = money.clientHeight;
    across = width * cols;
    card = document.getElementById('card');
    loop = setInterval(next, 1000 / 24);
}
	
function next() {
    money.style.backgroundPosition = (-xpos) + 'px ' + (-ypos) + 'px';
    xpos += width;
    index += 1;

    if (index == 15) {
        card.style.visibility = 'visible';
    }
    else if (index == frames) {
        clearInterval(loop);
    }
    else if (xpos + width > across) {
        xpos = 0;
        ypos += height;
    }
}

function resize() {
    var oldWidth = width,
		oldHeight = height;
    width = money.clientWidth;
    height = money.clientHeight;
	
    if (width != oldWidth || height != oldHeight) {
        xpos = xpos * width / oldWidth - width;
        ypos = ypos * height / oldHeight;
        across = width * cols;
        if (index == frames) {
            money.style.backgroundPosition = (-xpos) + 'px ' + (-ypos) + 'px';
        }
    }
}

window.onload = init;
window.onresize = resize;



/*
Client: Barclays
Product: Hawaiian Airlines Card

Sampled: shim for opacity on alpha, base64
Archive: http://hawaiian.cosgrovenystaging.com/consumer/
*/

var ie8 = !+'\v1';

$.fn.alpha = function() {
    if (ie8) {
        this.each(function() {
            var opacity = $(this).css('opacity'),
                alpha = (opacity != '1') ? 'alpha(opacity=' + opacity + '), ' : '',
                blank = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAQAIBRAA7';

            if (this.src != blank) {
                var src = $(this).attr('src');
                $(this)
                    .data('src', src)
                    .attr({
                        'src': blank,
                        'style': 'filter: ' + alpha + 'progid:DXImageTransform.Microsoft.AlphaImageLoader(sizingMethod="scale", src="' + src + '");'
                    });
            } else {
                $(this)
                    .attr('src', $(this).data('src'))
                    .removeAttr('style');
            }
        });
    } else {
        $(this).removeAttr('style');
    }
    return this;
}
