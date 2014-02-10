"use strict";

var $ = require('jquery'),
    _ = require('lodash'),
    FH = require('treemap/fieldHelpers'),
    U = require('treemap/utility'),
    Bacon = require('baconjs'),
    streetView = require('treemap/streetView'),
    otmTypeahead = require('treemap/otmTypeahead'),
    reverseGeocodeStreamAndUpdateAddressesOnForm =
        require('treemap/reverseGeocodeStreamAndUpdateAddressesOnForm'),
    geocoder = require('treemap/geocoder'),
    geocoderUi = require('treemap/geocoderUi'),
    enterOrClickEventStream = require('treemap/baconUtils').enterOrClickEventStream,
    diameterCalculator = require('treemap/diameterCalculator');

var config,
    mapManager,
    plotMarker,
    formSelector = '#add-tree-form',
    onClose,  // function to call when closing mode
    $sidebar,
    $addButton,
    $address,
    $form,
    $editFields,
    $editControls,
    $validationFields,
    $placeMarkerMessage,
    $moveMarkerMessage,
    deactivateBus,
    gcoder,
    $addTreeHeaderLink,
    $exploreTreesHeaderLink,
    prompter;

function init(options) {
    config = options.config;
    mapManager = options.mapManager;
    plotMarker = options.plotMarker;
    onClose = options.onClose || $.noop;
    $sidebar = options.$sidebar;
    gcoder = geocoder(config);
    prompter = options.prompter;

    $addTreeHeaderLink = options.$addTreeHeaderLink;
    $exploreTreesHeaderLink = options.$exploreTreesHeaderLink;

    var addressInput = '#add-tree-address',
        $geolocateButton = U.$find('.geolocate', $sidebar),
        $geocodeError = U.$find('.geocode-error', $sidebar),
        $geolocateError = U.$find('.geolocate-error', $sidebar),
        triggerSearchBus = options.triggerSearchBus;

    $form = U.$find(formSelector, $sidebar);
    $editFields = U.$find('[data-class="edit"]', $form);
    $editControls = $editFields.find('input,select');
    $validationFields = U.$find('[data-class="error"]', $form);
    $addButton = U.$find('.addBtn', $sidebar);
    $address = U.$find(addressInput, $sidebar);
    $placeMarkerMessage = U.$find('.place-marker-message', $sidebar);
    $moveMarkerMessage = U.$find('.move-marker-message', $sidebar);

    $editFields.show();
    U.$find('[data-class="display"]', $form).hide();  // Hide display fields

    otmTypeahead.bulkCreate(options.typeaheads);

    // Handle setting initial tree position via geolocate button
    var geolocateStream;
    if (navigator.geolocation) {
        geolocateStream = $geolocateButton
            .asEventStream('click')
            .flatMap(geolocate);
        geolocateStream
            .filter('.coords')
            .onValue(onGeolocateSuccess);
        geolocateStream.onError(function () {
            $geolocateError.show();
        });
    } else {
        geolocateStream = Bacon.never();
        $geolocateButton.prop('disabled', true);
    }

    // Handle user dragging the marker
    var markerFirstMoveStream = plotMarker.firstMoveStream.filter(options.inMyMode);
    markerFirstMoveStream.onValue(onMarkerMoved);

    // Handle user adding a tree
    var addTreeStream = $addButton.asEventStream('click');
    addTreeStream.onValue(addTree, [onAddTreeSuccess, triggerSearchBus.push]);

    // Handle user clicking "Cancel"
    var cancelStream = U.$find('.cancelBtn', $sidebar).asEventStream('click');
    cancelStream.onValue(onClose);

    // Handle internal and external deactivation
    deactivateBus = new Bacon.Bus();
    deactivateBus.plug(cancelStream);
    deactivateBus.onValue(function () {
        // Hide/deactivate/clear everything
        plotMarker.hide();
        $address.val("");
        $editControls.val("");
    });

    diameterCalculator({ formSelector: formSelector,
                         cancelStream: cancelStream,
                         saveOkStream: addTreeStream });


    // Handle setting initial tree position via address search
    var searchTriggerStream = enterOrClickEventStream({
            inputs: addressInput,
            button: '.geocode'
        }),
        addressStream = searchTriggerStream.map(function () {
            return $(addressInput).val();
        }),
        geocodeResponseStream = gcoder.geocodeStream(addressStream),
        cleanupLocationFeedbackStream = Bacon.mergeAll([
            searchTriggerStream,
            geolocateStream,
            markerFirstMoveStream,
            addTreeStream,
            deactivateBus
        ]),
        geocodedLocationStream = geocoderUi({
            geocodeResponseStream: geocodeResponseStream,
            cancelGeocodeSuggestionStream: cleanupLocationFeedbackStream,
            resultTemplate: '#geocode-results-template',
            addressInput: addressInput,
            displayedResults: '#sidebar-add-tree [data-class="geocode-result"]'
        });

    cleanupLocationFeedbackStream.onValue(function hideLocationErrors() {
        $geocodeError.hide();
        $geolocateError.hide();
    });
    geocodedLocationStream.onValue(onLocationChosen);
    geocodeResponseStream.onError(function () {
        $geocodeError.show();
    });

    var markerMoveStream = plotMarker.moveStream.filter(options.inMyMode);
    var reverseGeocodeStream =
            reverseGeocodeStreamAndUpdateAddressesOnForm(
                config, markerMoveStream, formSelector);

    reverseGeocodeStream.map(reverseGeocodeResponseToAddressString)
                        .onValue($address, 'val');
    reverseGeocodeStream.onError($address, 'val', '');

    if (options.config.instance.basemap.type === 'google') {
        var $streetViewContainer = $("#streetview");
        var container = null;

        markerMoveStream.onValue(function(latlng) {
            $streetViewContainer.show();
            if (!container) {
                container = streetView.create({
                    streetViewElem: $streetViewContainer[0],
                    noStreetViewText: options.config.noStreetViewText,
                    hideAddress: true,
                    location: latlng
                });
            }

            container.update(latlng);
        });

        deactivateBus.onValue(function () {
            $streetViewContainer.hide();
        });


    }
}

// Adding a tree uses a state machine with these states and transitions:
//
// Inactive:
//     activate() -> CanPlaceMarker
//
// CanPlaceMarker:
//     onLocationChosen() -> CanMoveMarker
//     cancel() -> Inactive
//     deactivate() -> Inactive
//
// CanMoveMarker:
//     onMarkerMoved() -> CanAddTree
//     cancel() -> Inactive
//     deactivate() -> Inactive
//
// CanAddTree:
//     onAddTreeSuccess() -> Inactive
//     cancel() -> Inactive
//     deactivate() -> Inactive

function activate() {
    $addTreeHeaderLink.addClass("active");
    $exploreTreesHeaderLink.removeClass("active");

    // Let user start creating a tree (by clicking the map)
    plotMarker.hide();
    plotMarker.enablePlacing();
    enableFormFields(false);
    $placeMarkerMessage.show();
}

function geolocate() {
    var options = {
        enableHighAccuracy: true,
        timeout: 5000,
        maximumAge: 0
    };
    var deferred = $.Deferred();
    navigator.geolocation.getCurrentPosition(deferred.resolve, deferred.reject, options);
    return Bacon.fromPromise(deferred.promise());
}

function onGeolocateSuccess(lonLat) {
    var location = U.lonLatToWebMercator(lonLat.coords.longitude, lonLat.coords.latitude);
    onLocationChosen(location);
}

function onLocationChosen(location) {
    // User has chosen an initial tree location via geocode or geolocate.
    // Show the marker (zoomed and centered), and let them drag it.
    // Show a message so they know the marker must be moved to continue.
    mapManager.setCenterAndZoomIn(location, mapManager.ZOOM_PLOT);
    plotMarker.place(location);
    requireMarkerDrag();
}

function requireMarkerDrag() {
    enableFormFields(false);
    plotMarker.enableMoving();
    $placeMarkerMessage.hide();
    $moveMarkerMessage.show();
}

function onMarkerMoved() {
    // User moved tree for the first time (or clicked the map). Let them edit fields.
    enableFormFields(true);
    _.defer(function () {
        $editControls.not('[type="hidden"]').first().focus().select();
    });
    $placeMarkerMessage.hide();
    $moveMarkerMessage.hide();
}

function enableFormFields(shouldEnable) {
    $addButton.prop('disabled', !shouldEnable);
    $editControls.prop('disabled', !shouldEnable);
}

function addTree(success) {
    // User hit "Add Tree".
    $validationFields.hide();
    var data = FH.formToDictionary($form, $editFields);
    data['plot.geom'] = plotMarker.getLocation();
    // Exclude null fields to allow defaults to be set by the server
    // If all tree fields are null, this will cause a plot w/o tree to be added
    _.each(data, function(value, key) {
        if (value === null) {
            delete data[key];
        }
    });

    $.ajax({
        url: config.instance.url + 'plots/',
        type: 'POST',
        contentType: "application/json",
        data: JSON.stringify(data),
        success: success,
        error: onAddTreeError
    });
}

function close() {
    deactivateBus.push();
    onClose();
}

function onAddTreeSuccess(result) {
    // Tree was saved. Update map if appropriate.
    mapManager.updateGeoRevHash(result.geoRevHash);
    var option = U.$find('input[name="addTreeOptions"]:checked', $sidebar).val();

    if (!result.enabled) {
        close();
        $('[data-feature="add_plot"]').hide();
        return;
    }

    switch (option) {
    case 'copy':
        requireMarkerDrag();
        break;
    case 'new':
        $editControls.val("");
        requireMarkerDrag();
        break;
    case 'edit':
        var url = config.instance.url + 'features/' + result.featureId + '/edit';
        prompter.unlock();
        window.location.hash = '';
        window.location.href = url;
        break;
    case 'close':
        close();
        break;
    }
}

function onAddTreeError(jqXHR, textStatus, errorThrown) {
    // Tree wasn't saved. Show validation errors.
    var errorDict = jqXHR.responseJSON.validationErrors;
    _.each(errorDict, function (errorList, fieldName) {
        FH.getField($validationFields, fieldName)
            .html(errorList.join(','))
            .show();
    });
}

function deactivate() {
    $addTreeHeaderLink.removeClass("active");
    $exploreTreesHeaderLink.addClass("active");

    // We're being deactivated by an external event
    deactivateBus.push();
}

function reverseGeocodeResponseToAddressString(reverseGeocodeResponse) {
    var a = reverseGeocodeResponse.address,
        street = a.Address,
        city = a.City,
        state = a.Region,
        postalCode = a.Postal;
    return street + ' ' + city + ' ' + state + ' ' + postalCode;
}

module.exports = {
    name: 'addTree',
    init: init,
    activate: activate,
    deactivate: deactivate,
    lockOnActivate: true
};
