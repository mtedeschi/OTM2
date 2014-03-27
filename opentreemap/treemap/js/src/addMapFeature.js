"use strict";

var $ = require('jquery'),
    _ = require('lodash'),
    FH = require('treemap/fieldHelpers'),
    U = require('treemap/utility'),
    Bacon = require('baconjs'),
    streetView = require('treemap/streetView'),
    reverseGeocodeStreamAndUpdateAddressesOnForm =
        require('treemap/reverseGeocodeStreamAndUpdateAddressesOnForm'),
    geocoder = require('treemap/geocoder'),
    geocoderUi = require('treemap/geocoderUi'),
    enterOrClickEventStream = require('treemap/baconUtils').enterOrClickEventStream;

function init(options) {
    var config = options.config,
        mapManager = options.mapManager,
        plotMarker = options.plotMarker,
        onClose = options.onClose || $.noop,
        sidebar = options.sidebar,
        $sidebar = $(sidebar),
        formSelector = options.formSelector,
        indexOfSetLocationStep = options.indexOfSetLocationStep,
        addFeatureRadioOptions = options.addFeatureRadioOptions,
        addFeatureUrl = config.instance.url + 'plots/',
        onSaveBefore = options.onSaveBefore || _.identity,
        gcoder = geocoder(config),

        $addFeatureHeaderLink = options.$addFeatureHeaderLink,
        $exploreMapHeaderLink = options.$exploreMapHeaderLink,

        stepControls = require('treemap/stepControls').init($sidebar),
        addressInput = sidebar + ' .form-search input',
        $addressInput = $(addressInput),
        $summaryAddress = U.$find('.summaryAddress', $sidebar),
        $geolocateButton = U.$find('.geolocate', $sidebar),
        $geocodeError = U.$find('.geocode-error', $sidebar),
        $geolocateError = U.$find('.geolocate-error', $sidebar),
        triggerSearchBus = options.triggerSearchBus,

        $form = U.$find(formSelector, $sidebar),
        editFields = formSelector + ' [data-class="edit"]',
        validationFields = formSelector + ' [data-class="error"]',
        $placeMarkerMessage = U.$find('.place-marker-message', $sidebar),
        $moveMarkerMessage = U.$find('.move-marker-message', $sidebar);

    $(editFields).show();
    $form.find('[data-class="display"]').hide();  // Hide display fields

    // Handle setting initial position via geolocate button
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

    // Handle user adding a feature
    var addFeatureStream = stepControls.allDoneStream;
    addFeatureStream.onValue(addFeature);

    // Handle user clicking "Cancel"
    var cancelStream = U.$find('.cancelBtn', $sidebar).asEventStream('click');
    cancelStream.onValue(onClose);

    // Handle internal and external deactivation
    var deactivateBus = new Bacon.Bus();
    deactivateBus.plug(cancelStream);
    deactivateBus.onValue(function () {
        // Hide/deactivate/clear everything
        plotMarker.hide();
        $addressInput.val("");
        clearEditControls();
    });

    // Handle moving to "set location" step
    stepControls.stepChangeCompleteStream.onValue(function (stepNumber) {
        if (stepNumber === indexOfSetLocationStep) {
            focusOnAddressInput();
        }
    });

    // Handle setting initial position via address search
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
            addFeatureStream,
            deactivateBus
        ]),
        geocodedLocationStream = geocoderUi({
            geocodeResponseStream: geocodeResponseStream,
            cancelGeocodeSuggestionStream: cleanupLocationFeedbackStream,
            resultTemplate: '#geocode-results-template',
            addressInput: addressInput,
            displayedResults: sidebar + ' [data-class="geocode-result"]'
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

    reverseGeocodeStream.onValue(function (response) {
        var a = response.address,
            street = a.Address,
            rest = a.City + ' ' + a.Region + ' ' + a.Postal;
        $addressInput.val(street + ' ' + rest);
        $summaryAddress.html(street + '<br/>' + rest);
    });
    reverseGeocodeStream.onError($addressInput, 'val', '');

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

    // Adding a feature uses a state machine with these states and transitions:
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
    //     onMarkerMoved() -> CanAddFeature
    //     cancel() -> Inactive
    //     deactivate() -> Inactive
    //
    // CanAddFeature:
    //     onAddFeatureSuccess() -> Inactive
    //     cancel() -> Inactive
    //     deactivate() -> Inactive

    function activate() {
        $addFeatureHeaderLink.addClass("active");
        $exploreMapHeaderLink.removeClass("active");
        plotMarker.hide();
        stepControls.showStep(0);
        stepControls.enableNext(indexOfSetLocationStep, false);
        $placeMarkerMessage.show();
    }

    function setAddFeatureUrl(url) {
        addFeatureUrl = url;
    }

    function focusOnAddressInput() {
        if ($addressInput.val().length === 0) {
            $addressInput.focus();
        }
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
        // User has chosen an initial location via geocode or geolocate.
        // Show the marker (zoomed and centered), and let them drag it.
        // Show a message so they know the marker must be moved to continue.
        mapManager.setCenterWM(location);
        plotMarker.place(location);
        requireMarkerDrag();
    }

    function requireMarkerDrag() {
        stepControls.enableNext(indexOfSetLocationStep, false);
        plotMarker.enableMoving();
        $placeMarkerMessage.hide();
        $moveMarkerMessage.show();
    }

    function onMarkerMoved() {
        // User moved marker for the first time (or clicked the map). Let them edit fields.
        stepControls.enableNext(indexOfSetLocationStep, true);
        $placeMarkerMessage.hide();
        $moveMarkerMessage.hide();
    }

    function addFeature() {
        // User hit "Done".
        $(validationFields).hide();
        var data = getFormData();
        data['plot.geom'] = plotMarker.getLocation();
        // Exclude null fields to allow defaults to be set by the server
        // If all tree fields are null, this will cause a plot w/o tree to be added
        _.each(data, function(value, key) {
            if (value === null) {
                delete data[key];
            }
        });

        $.ajax({
            url: addFeatureUrl,
            type: 'POST',
            contentType: "application/json",
            data: JSON.stringify(data),
            success: [onAddFeatureSuccess, triggerSearchBus.push],
            error: onAddFeatureError
        });
    }

    function getFormData() {
        var formData = FH.formToDictionary($form, $(editFields));
        onSaveBefore(formData);
        return formData;
    }

    function close() {
        deactivateBus.push();
        onClose();
    }

    function onAddFeatureSuccess(result) {
        // Feature was saved. Update map if appropriate.
        mapManager.updateGeoRevHash(result.geoRevHash);
        var option = U.$find('input[name="' + addFeatureRadioOptions + '"]:checked', $sidebar).val();

        if (!result.enabled) {
            close();
            $('[data-feature="add_plot"]').hide();
            return;
        }

        switch (option) {
        case 'copy':
            requireMarkerDrag();
            stepControls.showStep(0);
            break;
        case 'new':
            clearEditControls();
            requireMarkerDrag();
            stepControls.showStep(0);
            break;
        case 'edit':
            close();
            var url = config.instance.url + 'features/' + result.featureId + '/edit';
            window.location.hash = '';
            window.location.href = url;
            break;
        case 'close':
            close();
            break;
        }
    }

    function clearEditControls() {
        $(editFields).find('input,select').each(function () {
            var $control = $(this),
                type = $control.prop('type');
            if (type === 'checkbox' || type === 'radio') {
                $control.prop('checked', false);
            } else {
                $control.val("");
            }
        });
    }

    function onAddFeatureError(jqXHR, textStatus, errorThrown) {
        // Feature wasn't saved. Show validation errors.
        if (jqXHR.responseJSON) {
            var errorDict = jqXHR.responseJSON.validationErrors;
            _.each(errorDict, function (errorList, fieldName) {
                FH.getField($(validationFields), fieldName)
                    .html(errorList.join(','))
                    .show();
            });
            stepControls.showStep(stepControls.maxStepNumber - 1);
        }
    }

    function deactivate() {
        $addFeatureHeaderLink.removeClass("active");
        $exploreMapHeaderLink.addClass("active");

        // We're being deactivated by an external event
        deactivateBus.push();
    }

    return {
        activate: activate,
        deactivate: deactivate,
        setAddFeatureUrl: setAddFeatureUrl,
        focusOnAddressInput: focusOnAddressInput,
        getFormData: getFormData,
        stepControls: stepControls,
        addFeatureStream: addFeatureStream,
        deactivateStream: deactivateBus.map(_.identity)
    };
}

module.exports = {
    init: init
};
