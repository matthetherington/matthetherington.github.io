---
layout: post
title:  "Writing Maintainable React Tests with Jest & Enzyme"
---

When looking for approaches to testing React components, you will find a multitude - each with their own merits and drawbacks. I'm not sure whether a single, correct approach even exists but after writing a great many of these tests I've stumbled across a few helpful ideas that, when put into practice, will make your tests an asset rather than a nuisance.

As your application code grows, your test code will grow accordingly (hopefully anyway!) and often you'll have as much or more test code than application code. The sheer volume of this code makes keeping it maintainable a necessity.

Unit tests are a bit like an alarm system, when everything is ok they should be quiet and only when there is a clear and present danger should they alert you. If an alarm misses an intruder - that makes for a bad alarm, but sounding at every tiny movement makes for an equally bad alarm.

There's nothing worse than making a small, couple-line change to a component only to find that all the units tests now fail, the tests are poorly written and you don't understand what they are meant to be testing. Your couple-line change has now turned into an ordeal and when these situations compound it can greatly slow down development.

So how do we avoid situations like this? In no particular order:

## Don't repeat yourself

Ideally, your couple-line change to the application code should only result in a couple-line change to the tests. This becomes much harder to achieve when there is duplication in the tests. In this situation, when a change is required to the duplicated code, you will have N changes to make instead of 1. Sometimes the duplication can be overt, and other times it is more subtle.

Consider the following test suite:

```jsx
describe("CustomerPopup", () => {
  it("renders the correct message when there is a customer", () => {
    const fakeCustomer = { id: "foo" };
    const wrapper = shallow(
      <CustomerPopup customer={fakeCustomer} />
    );

    expect(wrapper.find(Popup).prop("message"))
      .toBe(i18n.t("customerList.modal.customerPopupMessage"));
  });

  it("should render the correct message when there is no customer", () => {
    const wrapper = shallow(<CustomerPopup customer={null} />);
    expect(wrapper.find(Popup).prop("message"))
      .toBe(i18n.t("customerList.modal.noCustomerMessage"));
  });
  
  //lots more tests here...

});
```

Let's say we need to make a change which requires adding a new required prop to `CustomerPopup`. We are going to have to amend each of the shallow calls and add in this new prop.

If we refactor the tests to remove the duplication, then the situation can be avoided and we get our "couple-line" test change:

```jsx
describe("CustomerPopup", () => {
  let props;
  
  beforeEach(() => {
    props = {
    fullScreen: true,
  };

  const shallowRender = () => shallow(<CustomerPopup {...props} />);
  const getPopupMessage = () => shallowRender().find(Popup).prop("message");

  describe("when there is a customer", () => {

    beforeEach(() => {
      props.customer = { id: "foo" };
    });
    
    it("renders the customer message", () => {
      expect(getPopupMessage())
        .toBe(i18n.t("customerList.modal.customerMessage"));
    });

  });

  describe("when there is no customer", () => {

    beforeEach(() => {
      props.customer = null;
    });

    it("renders the no customer message ", () => {
      expect(getPopupMessage())
        .toBe(i18n.t("customerList.modal.noCustomerMessage"));
    });

  });

});
```

By moving the shallow call and "DOM"-traversal to their own functions, we restrict the impact of any changes to the rendering or output to just those functions.

And by moving the setup into the beforeEach hooks, we make it easier to add new test cases in future should new functionality be added.

For example, we might want to show a different message if the customer is a corporate customer:

```jsx
describe("when there is a customer", () => {

  beforeEach(() => {
    props.customer = { id: "foo" };
  });

  it("renders the customer message", () => {
    expect(getPopupMessage())
      .toBe(i18n.t("customerList.modal.customerMessage"));
  });
  
  describe("and the customer is a corporate customer", () => {
    beforeEach(() => {
      props.customer.customerType = "corporate";
    });

    it("renders the corporate customer message", () => {
      expect(getPopupMessage())
        .toBe(i18n.t("customerList.modal.corporateMessage"));
    });
  });

});
```

## Test behaviour not implementation

Our tests should give us a high degree of confidence that a particular component is behaving correctly, and **only** that it is behaving correctly. If something behaves properly then it is of no consequence exactly how it is implemented.

A well-written test should allow us to refactor a component without having to change the test code. If the test code is identical to before and it still passes then we can be confident that we haven't introduced any new bugs. Any time we have to make a change to the test code while we are refactoring, we introduce the possibility of error.

Lets say we have the following button component which when clicked will disable itself:

```jsx
class OneClickButton extends React.Component {
  constructor(props) {
    super(props);
    this.state = { clicked: false };
  }

  handleClick = () => {
    this.setState({
      clicked: true,
    }, this.props.onClick);
  };

  render() {
    return (
      <button
        onClick={this.handleClick}
        disabled={this.state.clicked}
      >{this.props.children}</button>
    );
  };
}
```

The kind of testing that we want to avoid would look something like this:

```jsx
describe("OneClickButton", () => {

  it("sets clicked to false when handleClick is called", () => {
    const wrapper = shallow(<OneClickButton onClick={jest.fn()} />);
    wrapper.instance().handleClick();
    expect(wrapper.state("clicked")).toBe(true);
  });

});
```

If this were a statically-typed language, then `handleClick` would be a private member of `OneClickButton` and not form part of its public interface. Anything not part of the public interface is by definition an implementation detail and we should be free to change it as we see fit without breaking the tests. We are only interested in the behaviour.

In addition, a component's internal state is just that - internal. There should be no need to make assertions about a component's state, instead, make assertions about what the effect of the state change is.

So, bearing the above in mind, we can rewrite our test so that the behavior is tested instead:

```jsx
describe("OneClickButton", () => {
  let props;
  
  beforeEach(() => {
    props = {
      onClick: jest.fn(),
    };
  });
  
  const shallowRender = () => shallow(<OneClickButton {...props} />);

  it("disables itself when clicked", () => {
    wrapper.prop("onClick")();
    expect(shallowRender().prop("disabled")).toBe(true);
  });

});
```

It's a subtle change but writing tests in this way can give us the freedom to refactor with ease and be confident that our code is still working.

## Test one thing at a time

Consider the following test:

```jsx
it("configCustomKeys test", () => {
  const data = {
    getAgentTiles: { uri: "" },
    getCustomerTiles: { uri: "" },
    getCustomerTaskMenu: { uri: "" },
    getAgentTaskMenuAndTabs: { uri: "" }
  };

  const expected = {
    getAgentTiles: { customKey: "tiles", uri: "" },
    getCustomerTiles: { customKey: "tiles", uri: "" },
    getCustomerMenu: { customKey: "taskMenuTabs", uri: "" },
    getAgentMenu: { customKey: "taskMenuTabs", uri: "" }
  };

  expect(ServicesHelper.configCustomKeys(data)).toEqual(expected);
});
```

It looks like this test checks that the `configCustomKeys` method is doing four things:

* adding a  `customKey` of tiles to `getAgentTiles`
* adding a `customKey` of tiles to `getCustomerTiles`
* adding a `customKey` of taskMenuTabs to `getCustomerMenu`
* adding a `customKey` of taskMenuTabs to `getAgentMenu`

That means that there are at least four reasons that this test could fail. If this happens then as a developer tasked with fixing it, we are going to have to figure out which one of these situations we find ourselves in. 

Ignoring that fact that the method arguably shouldn't be doing four things anyway, we could make this process a lot easier for our future selves by splitting each of these cases out into their own tests within a describe block:

```jsx
describe("configCustomKeys", () => {
  it("adds a customKey of tiles for getAgentTiles", () => {
    expect(ServicesHelper.configCustomKeys({
      getAgentTiles: { uri: "" },
    })).toEqual({
      getAgentTiles: { customKey: "tiles", uri: "" },
    });
  });

  it("adds a customKey of tiles for getCustomerTiles", () => {
    expect(ServicesHelper.configCustomKeys({
      getCustomerTiles: { uri: "" },
    })).toEqual({
      getCustomerTiles: { customKey: "tiles", uri: "" },
    });
  });

  it("adds a customKey of taskMenuTabs for getCustomerMenu", () => {
    expect(ServicesHelper.configCustomKeys({
      getCustomerMenu: { uri: "" },
    })).toEqual({
      getCustomerMenu: { customKey: "taskMenuTabs", uri: "" },
    });
  });


  it("adds a customKey of taskMenuTabs for getAgentMenu", () => {
    expect(ServicesHelper.configCustomKeys(
      getAgentMenu: { uri: "" },
    })).toEqual({
      getAgentMenu: { customKey: "taskMenuTabs", uri: "" },
    });
  });
});
```

If a test fails now, we have a more accurate picture of what is going wrong and should be able to locate the offending piece of code more easily.

## Test the same thing your application consumes

Often, I come across modules which have a default export wrapped in several higher-order-components. The default export is what is consumed by the application code, but the test code imports the underlying component without the higher-order-components.

You run the risk here of having tests that pass but a broken application. Maybe there is some interaction between the higher-order-components that you hadn't considered, or maybe the order in which they are wrapped is important - this would be missed unless you test the default export.

This applies to redux-connected components too. While it is a sound strategy to named-export and test the React component and mapStateToProps individually, all-too-often I come across tests which only test the component and neglect mapStateToProps. 

The same points apply as in "Test behaviour not implementation". The important thing to test with a connected-component is that it receives the correct props from state, and that it dispatches the correct actions when interacted with - exactly how this is achieved doesn't really matter.

We can instantiate a mock store and give it to our connected-component, then test that our actions end up in the store like so:

```jsx
import mockStoreCreator from "redux-mock-store";
import { openCustomerList } from "./customer-list-actions";

const mockStore = mockStoreCreator()();

describe("OpenCustomerListButton", () => {
  let props;

  beforeEach(() => {
    mockStore.clearActions();
    props = {
      message: "fake-message",
    };
  });

  const shallowOptions = { context: { store: mockStore } };
  const shallowRender = () => shallow(
    <OpenCustomerListButton {...props} />,
    shallowOptions
  );

  it("opens the customer list when clicked", () => {
    shallowRender().prop("onClick")();
    expect(mockStore.getActions()).toContainEqual(openCustomerList())
  });

});
```

## Mock out dependencies

Tests become brittle when changes in a module can break tests in multiple different suites. To avoid this, we should only be testing the component-under-test, not any of its dependencies.

A good example of this are when a component uses redux selector functions. We don't want to be constructing a giant state object and passing this into our mock stores as we should already have tests around our selectors.

Testing them again is unnecessary and means that every time we want to change the shape of the state, we will have to make a corresponding change in each test that creates a fake state object. Simply mocking the return values from the selectors is enough.

## Make good use of \_\_mocks\_\_

While mocking can help to isolate our component-under-test, if there are many mocks of the same object spread throughout the codebase then this can again lead to brittle tests.

Imagine if you had a selector that is used by multiple different components, and the tests for each of these components have their own mock implementation of the selector.

If there was a change in the shape of the state returned by the selector then you would have to find every place that it is mocked and update the mock implementation.

To avoid this problem, jest provides us with the ability to write a mock implementation for a module in an adjacent __mocks__ folder, and when a call to `jest.mock("./path/to/module")` is made, this implementation will be used.

For example:

`customer-selectors.js`:
```jsx
export const getCustomerName = (state) => state.customer.name;
```

`\_\_mocks\_\_/customer-selectors`:
```jsx
export const getCustomerName = () => "fakeCustomerName";
```

`CustomerGreeting.js`
```jsx
import { getCustomerName } from "./customer-selectors";
const CustomerGreeting = ({ customerName }) => (
  <span>Hello {customerName}!</span>
);

const mapStateToProps = (state) => {
  customerName: getCustomerName(state),
};

export default connect(mapStateToProps)(CustomerGreeting);
```

`CustomerGreeting.spec.js`
```jsx
jest.mock("./customer-selectors");

import mockStoreCreator from "redux-mock-store";
import CustomerGreeting from "./CustomerGreeting";
import { getCustomerName } from "./customer-selectors";

const mockStore = mockStoreCreator()();

describe("CustomerGreeting", () => {

  let props;
  beforeEach(() => {
    props = {};
  });

  const shallowOptions = { context: { store: mockStore } };
  const shallowRender = () => shallow(
    <CustomerGreeting {...props} />,
    shallowOptions
  );

  it("should display a greeting with the customer name", () => {
    expect(shallowRender().text()).toBe(`Hello ${getCustomerName(null)}!`);
  });
});
```

## Conclusion

We should treat our test code just the same as we would treat our production code, giving careful consideration to what in your application might change in future and whether or not these changes might require a change in your test. By being defensive with your test code, you can largely insulate it from changes elsewhere in the application and keep your tests maintainable.

I hope this was helpful and I'd really appreciate any suggestions or feedback that you might have so please drop me an email at the address below with your comments.
